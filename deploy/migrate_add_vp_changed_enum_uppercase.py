#!/usr/bin/env python3
"""
Migration: Normalize VP_CHANGED to uppercase in EventType enum.

This consolidates previous lowercase/uppercase migrations and ensures the
enum label is VP_CHANGED.
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
    database_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
    print("WARNING: Using default DATABASE_URL")

DATABASE_URL = database_url

async def migrate():
    """Add VP_CHANGED (uppercase) to eventtype enum."""
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
        # Use connect() instead of begin() because ALTER TYPE ADD VALUE 
        # cannot be run inside a transaction block in PostgreSQL
        async with engine.connect() as conn:
            # First check if the enum type exists
            type_check = text("""
                SELECT EXISTS (
                    SELECT FROM pg_type 
                    WHERE typname = 'eventtype'
                );
            """)
            type_result = await conn.execute(type_check)
            type_exists = type_result.scalar()
            
            if not type_exists:
                print("Enum type 'eventtype' does not exist. Skipping migration.")
                print("  Note: The enum type should be created by the base schema (init_db.py)")
                return
            
            # Check for existing labels (upper and lower)
            upper_check = text("""
                SELECT enumlabel 
                FROM pg_enum 
                WHERE enumlabel = 'VP_CHANGED' 
                AND oid = (SELECT oid FROM pg_type WHERE typname = 'eventtype')
            """)
            lower_check = text("""
                SELECT enumlabel 
                FROM pg_enum 
                WHERE enumlabel = 'vp_changed' 
                AND oid = (SELECT oid FROM pg_type WHERE typname = 'eventtype')
            """)
            upper_exists = (await conn.execute(upper_check)).fetchone() is not None
            lower_exists = (await conn.execute(lower_check)).fetchone() is not None
            
            if upper_exists:
                print("Enum value 'VP_CHANGED' already exists. Skipping migration.")
                return
            
            if lower_exists:
                print("Enum value 'vp_changed' exists. Renaming to 'VP_CHANGED'...")
                try:
                    rename_query = text("""
                        ALTER TYPE eventtype RENAME VALUE 'vp_changed' TO 'VP_CHANGED'
                    """)
                    await conn.execute(rename_query)
                    await conn.commit()
                    print("Successfully renamed 'vp_changed' to 'VP_CHANGED'!")
                except Exception as e:
                    error_str = str(e).lower()
                    if 'duplicate' in error_str or 'already exists' in error_str:
                        print("Enum value 'VP_CHANGED' already exists (detected during rename). Skipping.")
                        await conn.commit()
                    else:
                        raise
                return
            
            # Add the new enum value if neither exists
            try:
                add_query = text("""
                    ALTER TYPE eventtype ADD VALUE 'VP_CHANGED'
                """)
                await conn.execute(add_query)
                await conn.commit()  # Explicit commit for ALTER TYPE
                print("Successfully added 'VP_CHANGED' to eventtype enum!")
            except Exception as e:
                error_str = str(e).lower()
                if 'duplicate' in error_str or 'already exists' in error_str:
                    print("Enum value 'VP_CHANGED' already exists (detected during add). Skipping.")
                    await conn.commit()
                else:
                    raise
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(migrate())
