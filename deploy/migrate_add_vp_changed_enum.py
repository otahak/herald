#!/usr/bin/env python3
"""
Migration: Add VP_CHANGED to EventType enum.

Run this once after deploying the new code to add the VP_CHANGED value
to the existing eventtype enum in PostgreSQL.
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
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        try:
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
                        print(f"Loaded DATABASE_URL from .env file")
                        break
        except Exception as e:
            print(f"Warning: Could not read .env file at {env_file}: {e}")

if not database_url or database_url.strip() == "":
    database_url = "postgresql+asyncpg://herald:CHANGE_ME@localhost:5432/herald"
    print("WARNING: Using default DATABASE_URL")

DATABASE_URL = database_url

async def migrate():
    """Add VP_CHANGED to eventtype enum."""
    engine = create_async_engine(DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        # Check if VP_CHANGED already exists in the enum
        check_query = text("""
            SELECT enumlabel 
            FROM pg_enum 
            WHERE enumlabel = 'vp_changed' 
            AND oid = (SELECT oid FROM pg_type WHERE typname = 'eventtype')
        """)
        result = await conn.execute(check_query)
        exists = result.fetchone() is not None
        
        if exists:
            print("Enum value 'vp_changed' already exists. Skipping migration.")
        else:
            # Add the new enum value
            alter_query = text("""
                ALTER TYPE eventtype ADD VALUE IF NOT EXISTS 'vp_changed'
            """)
            await conn.execute(alter_query)
            print("Successfully added 'vp_changed' to eventtype enum!")
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(migrate())
