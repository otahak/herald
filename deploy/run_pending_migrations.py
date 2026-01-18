#!/usr/bin/env python3
"""
Run pending migrations automatically.

This script checks which migrations have already been applied and runs only new ones.
Safe to run multiple times - migrations are idempotent.
"""
import asyncio
import os
import sys
import subprocess
import socket
import re
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Get the deploy directory
DEPLOY_DIR = Path(__file__).parent
PROJECT_ROOT = DEPLOY_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

def load_env():
    """Load .env file if it exists."""
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def adjust_database_url(database_url: str) -> str:
    """Adjust database URL for Docker vs host execution."""
    # Check if we can resolve 'db' hostname (most reliable Docker detection)
    can_resolve_db = False
    try:
        socket.gethostbyname("db")
        can_resolve_db = True
    except (socket.gaierror, OSError):
        pass
    
    has_dockerenv = os.path.exists("/.dockerenv")
    hostname = socket.gethostname()
    is_container_hostname = hostname in ["herald", "herald-db"]
    
    is_inside_docker = can_resolve_db or has_dockerenv or is_container_hostname
    
    if database_url:
        if is_inside_docker:
            if re.search(r'@(localhost|127\.0\.0\.1):', database_url):
                database_url = re.sub(r'@(localhost|127\.0\.0\.1):', r'@db:', database_url)
            elif "@db:" not in database_url:
                database_url = re.sub(r'@[^:]+:', r'@db:', database_url)
        else:
            if "@db:" in database_url:
                database_url = database_url.replace("@db:", "@localhost:")
    
    return database_url

async def check_enum_value_exists(engine, enum_type: str, enum_value: str) -> bool:
    """Check if an enum value exists in the database."""
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT 1 
            FROM pg_enum 
            WHERE enumlabel = :value
            AND enumtypid = (SELECT oid FROM pg_type WHERE typname = :type)
        """), {"value": enum_value, "type": enum_type})
        return result.scalar_one_or_none() is not None

async def check_column_exists(engine, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    async with engine.connect() as conn:
        # Check database type
        db_url = str(engine.url)
        if "sqlite" in db_url.lower():
            # SQLite: use PRAGMA table_info
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            columns = result.fetchall()
            return any(col[1] == column for col in columns)
        else:
            # PostgreSQL: use information_schema
            result = await conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = :table AND column_name = :column
            """), {"table": table, "column": column})
            return result.scalar_one_or_none() is not None

async def check_migration_status(engine) -> dict:
    """Check which migrations have been applied."""
    status = {
        "has_solo_mode": False,
        "has_expiration": False,
        "has_unit_detached": False,
        "has_unit_actions": False,
    }
    
    # Check for solo mode column
    status["has_solo_mode"] = await check_column_exists(engine, "games", "is_solo")
    
    # Check for expiration tracking
    status["has_expiration"] = await check_column_exists(engine, "games", "last_activity_at")
    
    # Check for unit_detached enum
    status["has_unit_detached"] = await check_enum_value_exists(engine, "eventtype", "UNIT_DETACHED")
    
    # Check for unit action enums
    unit_action_enums = ["UNIT_RUSHED", "UNIT_ADVANCED", "UNIT_HELD", "UNIT_CHARGED", "UNIT_ATTACKED"]
    all_present = True
    for enum_val in unit_action_enums:
        if not await check_enum_value_exists(engine, "eventtype", enum_val):
            all_present = False
            break
    status["has_unit_actions"] = all_present
    
    return status

async def run_migration_script(script_path: Path, database_url: str) -> bool:
    """Run a migration script."""
    print(f"Running {script_path.name}...")
    
    # Use uv run if available, otherwise python
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        cmd = [str(venv_python), str(script_path)]
    else:
        # Try uv
        uv_paths = ["/usr/local/bin/uv", "/root/.cargo/bin/uv", "/home/herald/.cargo/bin/uv"]
        uv_cmd = None
        for path in uv_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                uv_cmd = path
                break
        
        if uv_cmd:
            env_file = PROJECT_ROOT / ".env"
            if env_file.exists():
                cmd = [uv_cmd, "run", "--env-file", str(env_file), "python", str(script_path)]
            else:
                cmd = [uv_cmd, "run", "python", str(script_path)]
        else:
            cmd = [sys.executable, str(script_path)]
    
    env = {**os.environ, "DATABASE_URL": database_url, "PYTHONPATH": str(PROJECT_ROOT)}
    
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        print(f"❌ {script_path.name} failed:")
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return False
    
    print(f"✓ {script_path.name} completed")
    return True

async def main():
    """Check and run pending migrations."""
    load_env()
    
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
    )
    database_url = adjust_database_url(database_url)
    
    print("Checking migration status...")
    engine = create_async_engine(database_url, echo=False)
    
    try:
        status = await check_migration_status(engine)
        
        migrations_to_run = []
        
        # Check which migrations are needed
        if not status["has_solo_mode"]:
            migrations_to_run.append(DEPLOY_DIR / "migrate_add_solo_mode.py")
        
        if not status["has_expiration"]:
            migrations_to_run.append(DEPLOY_DIR / "migrate_add_game_expiration.py")
        
        if not status["has_unit_detached"]:
            migrations_to_run.append(DEPLOY_DIR / "migrate_add_unit_detached_enum.py")
        
        if not status["has_unit_actions"]:
            migrations_to_run.append(DEPLOY_DIR / "migrate_add_unit_action_events.py")
        
        if not migrations_to_run:
            print("✓ All migrations are up to date")
            return 0
        
        print(f"\nFound {len(migrations_to_run)} pending migration(s):")
        for mig in migrations_to_run:
            print(f"  - {mig.name}")
        
        print("\nRunning pending migrations...")
        failed = []
        for migration in migrations_to_run:
            success = await run_migration_script(migration, database_url)
            if not success:
                failed.append(migration.name)
        
        if failed:
            print(f"\n❌ {len(failed)} migration(s) failed:")
            for name in failed:
                print(f"  - {name}")
            return 1
        else:
            print(f"\n✓ All {len(migrations_to_run)} migration(s) completed successfully!")
            return 0
            
    except Exception as e:
        print(f"❌ Error checking migration status: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await engine.dispose()

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
