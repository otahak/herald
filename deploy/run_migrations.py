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
    
    # Run the migration script
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
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
