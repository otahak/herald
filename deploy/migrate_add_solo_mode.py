"""Migration: Add is_solo field to games table."""

import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import os
from pathlib import Path

# Load environment variables
def load_env():
    """Load .env file if it exists."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def migrate():
    """Add is_solo column to games table."""
    load_env()
    
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
    )
    
    # If DATABASE_URL uses 'db' as hostname (Docker service), replace with localhost
    # This allows running migrations from host machine when Docker exposes port 5432
    if database_url and "@db:" in database_url:
        print("Note: DATABASE_URL uses 'db' hostname (Docker service name)")
        print("Replacing with 'localhost' for host machine access...")
        database_url = database_url.replace("@db:", "@localhost:")
        print(f"Using: {database_url.split('@')[0]}@localhost:5432/herald")
    
    engine = create_async_engine(database_url, echo=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Check if column already exists
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='games' AND column_name='is_solo'
            """)
            result = await session.execute(check_query)
            exists = result.scalar_one_or_none() is not None
            
            if exists:
                print("Column 'is_solo' already exists in 'games' table. Skipping migration.")
                return
            
            # Add is_solo column with default False
            await session.execute(text("""
                ALTER TABLE games 
                ADD COLUMN is_solo BOOLEAN NOT NULL DEFAULT FALSE
            """))
            
            await session.commit()
            print("✓ Successfully added 'is_solo' column to 'games' table")
            
        except Exception as e:
            await session.rollback()
            print(f"✗ Migration failed: {e}")
            raise
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
