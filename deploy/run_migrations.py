#!/usr/bin/env python3
"""
Automatic migration runner.

Discovers and runs all migration scripts in the deploy/ directory.
Migrations are run in alphabetical order and are idempotent (safe to run multiple times).

By default, the database persists between runs. Use --reset-db to drop and recreate
the database before running migrations (useful for troubleshooting).
"""
import argparse
import asyncio
import os
import sys
import subprocess
import re
from urllib.parse import urlparse, urlunparse
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Get the deploy directory
DEPLOY_DIR = Path(__file__).parent
PROJECT_ROOT = DEPLOY_DIR.parent


def load_env_file():
    """Load environment variables from .env file if it exists."""
    env_file = PROJECT_ROOT / ".env"
    env_vars = {}
    
    # Try multiple possible locations
    possible_locations = [
        env_file,  # /opt/herald/.env
        Path("/opt/herald/.env"),  # Absolute path
        PROJECT_ROOT.parent / ".env",  # Parent directory
    ]
    
    env_file_found = None
    for loc in possible_locations:
        if loc.exists() and loc.is_file():
            env_file_found = loc
            break
    
    if env_file_found:
        print(f"Loading environment variables from {env_file_found}")
        try:
            with open(env_file_found, "r") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith("#"):
                        continue
                    # Parse KEY=VALUE format
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        # Remove quotes from value if present
                        value = value.strip()
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]
                        # Store the value (even if empty, so we know it exists)
                        env_vars[key] = value
                        # Don't print sensitive values, but show we loaded them
                        if key == "DATABASE_URL":
                            # Show first and last few chars for debugging
                            db_url = value
                            if len(db_url) > 50:
                                print(f"  Loaded {key} = {db_url[:20]}...{db_url[-10:]}")
                            else:
                                print(f"  Loaded {key} = (hidden, length: {len(db_url)})")
                            # Check if empty
                            if not db_url:
                                print(f"  WARNING: {key} is empty in .env file!")
                        else:
                            print(f"  Loaded {key}")
        except Exception as e:
            print(f"ERROR: Failed to read .env file at {env_file_found}: {e}")
    else:
        print(f"WARNING: .env file not found. Tried:")
        for loc in possible_locations:
            print(f"  - {loc} (exists: {loc.exists()})")
    
    return env_vars

def find_migration_scripts():
    """Find all migration scripts in the deploy directory."""
    migrations = []
    for file in sorted(DEPLOY_DIR.glob("migrate_*.py")):
        if file.name != "run_migrations.py":  # Don't run ourselves
            migrations.append(file)
    return migrations

def _validate_identifier(value: str, label: str) -> str:
    if not value or not re.match(r"^[A-Za-z0-9_]+$", value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value

def _parse_database_url(database_url: str) -> tuple[str, str, str]:
    parsed = urlparse(database_url)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise ValueError("DATABASE_URL is missing a database name")
    user = parsed.username or ""
    if not user:
        raise ValueError("DATABASE_URL is missing a username")
    return db_name, user, urlunparse(parsed._replace(path="/postgres"))

async def reset_database(database_url: str) -> None:
    """Drop and recreate the database on every run."""
    db_name, db_user, admin_url = _parse_database_url(database_url)
    _validate_identifier(db_name, "database name")
    _validate_identifier(db_user, "database user")

    print(f"Resetting database '{db_name}' (owner: {db_user})...")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :db_name AND pid <> pg_backend_pid();"
                ),
                {"db_name": db_name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            await conn.execute(text(f'CREATE DATABASE "{db_name}" OWNER "{db_user}"'))
        print("✓ Database reset complete")
    finally:
        await engine.dispose()

def _build_script_command(script_path: Path, env: dict) -> tuple[list[str], dict]:
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), str(script_path)], env

    uv_cmd = None
    for path in ["/usr/local/bin/uv", "/root/.cargo/bin/uv", "/home/herald/.cargo/bin/uv"]:
        if os.path.exists(path) and os.access(path, os.X_OK):
            uv_cmd = path
            break

    if uv_cmd:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            return [uv_cmd, "run", "--env-file", str(env_file), "python", str(script_path)], env
        return [uv_cmd, "run", "python", str(script_path)], env

    return [sys.executable, str(script_path)], env

async def run_migration(script_path: Path):
    """Run a single migration script."""
    print(f"\n{'='*60}")
    print(f"Running migration: {script_path.name}")
    print(f"{'='*60}")
    
    # Load environment variables from .env file
    env_vars = load_env_file()
    
    # Merge with existing environment (env_vars take precedence)
    env = {**os.environ, **env_vars, "PYTHONPATH": str(PROJECT_ROOT)}
    
    # Debug: verify DATABASE_URL is set
    if "DATABASE_URL" in env:
        db_url = env["DATABASE_URL"]
        if len(db_url) > 50:
            print(f"DATABASE_URL will be passed to subprocess: {db_url[:20]}...{db_url[-10:]}")
        else:
            print("DATABASE_URL will be passed to subprocess: (set)")
    else:
        print("WARNING: DATABASE_URL not found in environment!")
        print("This migration may fail. Check that .env file exists and contains DATABASE_URL.")
    
    cmd, subprocess_env = _build_script_command(script_path, env)
    
    # Run the migration script
    print(f"Running command: {' '.join(cmd)}")
    print(f"Working directory: {PROJECT_ROOT}")
    print(f"DATABASE_URL in env: {'SET' if 'DATABASE_URL' in subprocess_env else 'NOT SET'}")
    if 'DATABASE_URL' in subprocess_env:
        db_val = subprocess_env['DATABASE_URL']
        if db_val:
            print(f"DATABASE_URL length: {len(db_val)}")
            # Extract hostname for DNS check
            if db_val.startswith("postgresql"):
                try:
                    # Parse: postgresql+asyncpg://user:pass@host:port/db
                    parts = db_val.split("@")
                    if len(parts) == 2:
                        host_part = parts[1].split("/")[0]  # host:port
                        hostname = host_part.split(":")[0]
                        print(f"  Hostname from DATABASE_URL: {hostname}")
                        # Test DNS resolution
                        import socket
                        try:
                            socket.gethostbyname(hostname)
                            print(f"  ✓ Hostname '{hostname}' resolves successfully")
                        except socket.gaierror as e:
                            print(f"  ✗ Hostname '{hostname}' cannot be resolved: {e}")
                            print(f"  Suggestion: Use 'localhost' or '127.0.0.1' if database is on same server")
                except Exception as e:
                    print(f"  Could not parse DATABASE_URL for hostname: {e}")
        else:
            print("WARNING: DATABASE_URL is empty string!")
    
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=subprocess_env,
        capture_output=True,
        text=True,
    )
    
    # Print output - always show both stdout and stderr
    print("=" * 60)
    if result.stdout:
        print("=== STDOUT ===")
        print(result.stdout)
    else:
        print("=== STDOUT ===")
        print("(empty)")
    
    if result.stderr:
        print("=== STDERR ===", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
    else:
        print("=== STDERR ===")
        print("(empty)")
    print("=" * 60)
    
    if result.returncode != 0:
        print(f"❌ Migration {script_path.name} failed with exit code {result.returncode}")
        if not result.stdout and not result.stderr:
            print("  WARNING: No output captured from migration script!")
            print("  This might indicate the script failed to start or was killed.")
            print(f"  Command was: {' '.join(cmd)}")
            print(f"  Working directory: {PROJECT_ROOT}")
        return False
    else:
        print(f"✅ Migration {script_path.name} completed successfully")
        return True

async def main(reset_db: bool = False):
    """Run all migrations."""
    print("="*60)
    print("Herald Database Migration Runner")
    print("="*60)
    
    migrations = find_migration_scripts()
    
    if not migrations:
        print("No migration scripts found in deploy/ directory.")
        return 0
    
    print(f"\nFound {len(migrations)} migration script(s):")
    for mig in migrations:
        print(f"  - {mig.name}")
    
    env_vars = load_env_file()
    env = {**os.environ, **env_vars, "PYTHONPATH": str(PROJECT_ROOT)}
    database_url = env.get("DATABASE_URL")
    
    if reset_db:
        if not database_url:
            print("ERROR: DATABASE_URL not found. Cannot reset database.")
            return 1
        
        print("\nResetting database (drop/recreate) ...")
        await reset_database(database_url)

        print("\nInitializing base schema...")
        init_script = DEPLOY_DIR / "init_db.py"
        init_cmd, init_env = _build_script_command(init_script, env)
        init_result = subprocess.run(
            init_cmd,
            cwd=str(PROJECT_ROOT),
            env=init_env,
            capture_output=True,
            text=True,
        )
        if init_result.returncode != 0:
            print("❌ init_db.py failed:")
            print(init_result.stdout)
            print(init_result.stderr, file=sys.stderr)
            return 1
        print("✓ Base schema initialized")
    else:
        print("\nSkipping database reset (database will persist)")
        print("Use --reset-db to drop and recreate the database")

    print("\nStarting migrations...")
    
    failed = []
    for migration in migrations:
        success = await run_migration(migration)
        if not success:
            failed.append(migration.name)
    
    print("\n" + "="*60)
    if failed:
        print(f"❌ {len(failed)} migration(s) failed:")
        for name in failed:
            print(f"  - {name}")
        return 1
    else:
        print(f"✅ All {len(migrations)} migration(s) completed successfully!")
        return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run database migrations for Herald",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run migrations normally (database persists):
  python deploy/run_migrations.py
  
  # Drop and recreate database, then run migrations:
  python deploy/run_migrations.py --reset-db
        """
    )
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="Drop and recreate the database before running migrations (destructive!)"
    )
    args = parser.parse_args()
    
    exit_code = asyncio.run(main(reset_db=args.reset_db))
    sys.exit(exit_code)
