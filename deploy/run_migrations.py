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
                    # Skip empty values (but still store them to show they were found)
                    if value:
                        env_vars[key] = value
                    else:
                        # Empty value - this is a problem
                        print(f"  WARNING: {key} is empty in .env file!")
                        env_vars[key] = value  # Store empty string so we know it exists but is empty
                        # Don't print sensitive values, but show we loaded them
                        if key == "DATABASE_URL":
                            # Show first and last few chars for debugging
                            db_url = value
                            if len(db_url) > 50:
                                print(f"  Loaded {key} = {db_url[:20]}...{db_url[-10:]}")
                            else:
                                print(f"  Loaded {key} = (hidden, length: {len(db_url)})")
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
        # For venv python, pass the full env dict
        subprocess_env = env
    else:
        # Fallback: try uv run, then system python
        uv_cmd = None
        for path in ["/usr/local/bin/uv", "/root/.cargo/bin/uv", "/home/herald/.cargo/bin/uv"]:
            if os.path.exists(path) and os.access(path, os.X_OK):
                uv_cmd = path
                break
        
        if uv_cmd:
            # When using uv run, explicitly pass --env-file to load .env
            # uv run will load the .env file, but we also pass env to ensure PYTHONPATH is set
            env_file = PROJECT_ROOT / ".env"
            if env_file.exists():
                cmd = [uv_cmd, "run", "--env-file", str(env_file), "python", str(script_path)]
            else:
                # Fallback: try without --env-file (should still inherit from env dict)
                cmd = [uv_cmd, "run", "python", str(script_path)]
            # uv run handles env loading, but we still pass env for PYTHONPATH and other vars
            subprocess_env = env
        else:
            # Last resort: system python
            cmd = [sys.executable, str(script_path)]
            subprocess_env = env
    
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
