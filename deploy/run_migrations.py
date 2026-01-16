#!/usr/bin/env python3
"""
Automatic migration runner.

Discovers and runs all migration scripts in the deploy/ directory.
Migrations are run in alphabetical order and are idempotent (safe to run multiple times).
"""
import asyncio
import os
import sys
import subprocess
from pathlib import Path

# Get the deploy directory
DEPLOY_DIR = Path(__file__).parent
PROJECT_ROOT = DEPLOY_DIR.parent


def load_env_file():
    """Load environment variables from .env file if it exists."""
    env_file = PROJECT_ROOT / ".env"
    env_vars = {}
    
    if env_file.exists():
        print(f"Loading environment variables from {env_file}")
        with open(env_file) as f:
            for line in f:
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
                    env_vars[key] = value
                    # Don't print sensitive values, but show we loaded them
                    if key == "DATABASE_URL":
                        # Show first and last few chars for debugging
                        db_url = value
                        if len(db_url) > 50:
                            print(f"  Loaded {key} = {db_url[:20]}...{db_url[-10:]}")
                        else:
                            print(f"  Loaded {key} = (hidden)")
                    else:
                        print(f"  Loaded {key}")
    else:
        print(f"WARNING: .env file not found at {env_file}")
    
    return env_vars

def find_migration_scripts():
    """Find all migration scripts in the deploy directory."""
    migrations = []
    for file in sorted(DEPLOY_DIR.glob("migrate_*.py")):
        if file.name != "run_migrations.py":  # Don't run ourselves
            migrations.append(file)
    return migrations

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
    
    # Prefer using venv python if it exists (most reliable)
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        cmd = [str(venv_python), str(script_path)]
    else:
        # Fallback: try uv run, then system python
        uv_cmd = None
        for path in ["/usr/local/bin/uv", "/root/.cargo/bin/uv", "/home/herald/.cargo/bin/uv"]:
            if os.path.exists(path) and os.access(path, os.X_OK):
                uv_cmd = path
                break
        
        if uv_cmd:
            # When using uv run, we need to ensure env vars are passed
            # uv run inherits from parent, so setting env in subprocess.run should work
            cmd = [uv_cmd, "run", "python", str(script_path)]
        else:
            # Last resort: system python
            cmd = [sys.executable, str(script_path)]
    
    # Run the migration script
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    
    # Print output
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    
    if result.returncode != 0:
        print(f"❌ Migration {script_path.name} failed with exit code {result.returncode}")
        return False
    else:
        print(f"✅ Migration {script_path.name} completed successfully")
        return True

async def main():
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
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
