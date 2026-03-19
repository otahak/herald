#!/usr/bin/env python3
"""
Migration: Add spells column to players table (JSON, nullable).
Spells are list of {name, cost, description} for caster units.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).parent.parent
database_url = os.getenv("DATABASE_URL")
if not database_url:
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
if not database_url:
    database_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"

async def migrate():
    engine = create_async_engine(database_url, echo=True)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'players' AND column_name = 'spells'
            """))
            if result.fetchone():
                print("Column 'spells' already exists. Skipping.")
                return
            await conn.execute(text("ALTER TABLE players ADD COLUMN spells JSONB NULL"))
            print("Added 'spells' column to players table.")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(migrate())
