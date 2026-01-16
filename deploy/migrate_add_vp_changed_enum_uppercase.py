#!/usr/bin/env python3
"""
Migration: Add VP_CHANGED (uppercase) to EventType enum.

This matches the existing database enum pattern (uppercase names).
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://herald:CHANGE_ME@localhost:5432/herald"
)

async def migrate():
    """Add VP_CHANGED (uppercase) to eventtype enum."""
    engine = create_async_engine(DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        # Check if VP_CHANGED already exists in the enum
        check_query = text("""
            SELECT enumlabel 
            FROM pg_enum 
            WHERE enumlabel = 'VP_CHANGED' 
            AND oid = (SELECT oid FROM pg_type WHERE typname = 'eventtype')
        """)
        result = await conn.execute(check_query)
        exists = result.fetchone() is not None
        
        if exists:
            print("Enum value 'VP_CHANGED' already exists. Skipping migration.")
        else:
            # Add the new enum value
            alter_query = text("""
                ALTER TYPE eventtype ADD VALUE IF NOT EXISTS 'VP_CHANGED'
            """)
            await conn.execute(alter_query)
            print("Successfully added 'VP_CHANGED' to eventtype enum!")
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(migrate())
