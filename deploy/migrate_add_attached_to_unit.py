#!/usr/bin/env python3
"""
Migration: Add attached_to_unit_id column to units table.

Run this once after deploying the new code to add the attached_to_unit_id column
to existing units tables.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Import models to use their metadata for table creation
from app.models import Base
from app.models.game import Game
from app.models.player import Player
from app.models.unit import Unit

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
    """Add attached_to_unit_id column to units table."""
    print(f"Using DATABASE_URL (length: {len(DATABASE_URL)})")
    if DATABASE_URL.startswith("postgresql"):
        parts = DATABASE_URL.split("@")
        if len(parts) == 2:
            # Show connection details without password
            conn_part = parts[0].split('//')[0]
            host_part = parts[1].split('/')[0]  # host:port
            db_part = parts[1].split('/')[1] if '/' in parts[1] else 'unknown'
            print(f"  Connection: {conn_part}//***@{host_part}/{db_part}")
            # Extract hostname for DNS check
            hostname = host_part.split(':')[0]
            print(f"  Hostname: {hostname}")
            print(f"  Database: {db_part}")
    
    try:
        engine = create_async_engine(DATABASE_URL, echo=True)
        print("✓ Database engine created successfully")
    except Exception as e:
        print(f"ERROR: Failed to create database engine: {e}")
        print(f"  DATABASE_URL value: {repr(DATABASE_URL)}")
        raise
    
    print("Attempting to connect to database...")
    try:
        async with engine.begin() as conn:
            # First check if table exists
            table_check = text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'units'
                );
            """)
            table_result = await conn.execute(table_check)
            table_exists = table_result.scalar()
            
            if not table_exists:
                print("Table 'units' does not exist. Creating it with base schema...")
                # Use Base.metadata.create_all to create tables in proper order with foreign keys
                # This ensures dependencies are created first
                await conn.run_sync(
                    Base.metadata.create_all,
                    tables=[Game.__table__, Player.__table__, Unit.__table__],
                    checkfirst=True
                )
                print("✓ Created 'units' table with all columns (including attached_to_unit_id)")
                
                # Create index for the column (since table was just created, index may not exist)
                index_query = text("""
                    CREATE INDEX IF NOT EXISTS ix_units_attached_to_unit_id 
                    ON units(attached_to_unit_id)
                """)
                await conn.execute(index_query)
                print("✓ Created index on attached_to_unit_id")
                # Column already exists, so skip the ALTER below
                return
            
            # Check if column already exists
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='units' AND column_name='attached_to_unit_id'
            """)
            result = await conn.execute(check_query)
            exists = result.fetchone() is not None
            
            if exists:
                print("Column 'attached_to_unit_id' already exists. Skipping migration.")
            else:
                # Add the column
                alter_query = text("""
                    ALTER TABLE units 
                    ADD COLUMN attached_to_unit_id UUID,
                    ADD CONSTRAINT fk_units_attached_to_unit 
                        FOREIGN KEY (attached_to_unit_id) 
                        REFERENCES units(id) 
                        ON DELETE SET NULL
                """)
                await conn.execute(alter_query)
                
                # Add index for performance
                index_query = text("""
                    CREATE INDEX IF NOT EXISTS ix_units_attached_to_unit_id 
                    ON units(attached_to_unit_id)
                """)
                await conn.execute(index_query)
                
                print("Successfully added 'attached_to_unit_id' column to units table!")
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(migrate())
