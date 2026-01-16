#!/usr/bin/env python3
"""
Migration: Add victory_points column to players table.

Run this once after deploying the new code to add the victory_points column
to existing players tables.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Load DATABASE_URL from environment or .env file
PROJECT_ROOT = Path(__file__).parent.parent
database_url = os.getenv("DATABASE_URL")

if not database_url:
    # Try loading from .env file - check multiple possible locations
    possible_locations = [
        PROJECT_ROOT / ".env",
        Path("/opt/herald/.env"),
        PROJECT_ROOT.parent / ".env",
    ]
    
    for env_file in possible_locations:
        if env_file.exists() and env_file.is_file():
            try:
                print(f"Attempting to read .env from: {env_file}")
                with open(env_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("DATABASE_URL="):
                            database_url = line.split("=", 1)[1].strip()
                            if database_url.startswith('"') and database_url.endswith('"'):
                                database_url = database_url[1:-1]
                            elif database_url.startswith("'") and database_url.endswith("'"):
                                database_url = database_url[1:-1]
                            print(f"✓ Loaded DATABASE_URL from .env file: {env_file}")
                            break
                if database_url:
                    break
            except Exception as e:
                print(f"Warning: Could not read .env file at {env_file}: {e}")
    
    if not database_url:
        print(f"Warning: .env file not found. Tried:")
        for loc in possible_locations:
            print(f"  - {loc} (exists: {loc.exists()}, is_file: {loc.is_file() if loc.exists() else False})")

if not database_url or database_url.strip() == "":
    database_url = "postgresql+asyncpg://herald:CHANGE_ME@localhost:5432/herald"
    print("WARNING: Using default DATABASE_URL")

DATABASE_URL = database_url

async def migrate():
    """Add victory_points column to players table."""
    print(f"Using DATABASE_URL (length: {len(DATABASE_URL)})")
    if DATABASE_URL.startswith("postgresql"):
        parts = DATABASE_URL.split("@")
        if len(parts) == 2:
            print(f"  Connection: {parts[0].split('//')[0]}//***@{parts[1]}")
    try:
        engine = create_async_engine(DATABASE_URL, echo=True)
    except Exception as e:
        print(f"ERROR: Failed to create database engine: {e}")
        print(f"  DATABASE_URL value: {repr(DATABASE_URL)}")
        raise
    
    print("Attempting to connect to database...")
    try:
        async with engine.begin() as conn:
            # Check if column already exists
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='players' AND column_name='victory_points'
            """)
            result = await conn.execute(check_query)
            exists = result.fetchone() is not None
            
            if exists:
                print("Column 'victory_points' already exists. Skipping migration.")
            else:
                # Add the column
                alter_query = text("""
                    ALTER TABLE players 
                    ADD COLUMN victory_points INTEGER NOT NULL DEFAULT 0
                """)
                await conn.execute(alter_query)
                print("Successfully added 'victory_points' column to players table!")
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(migrate())
