"""Migration: Add game expiration tracking (last_activity_at field and EXPIRED status)."""

import asyncio
import re
import socket
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
    """Add last_activity_at column and EXPIRED status to games table."""
    load_env()
    
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
    )
    
    # Check if we can resolve 'db' hostname (most reliable Docker detection)
    # If 'db' resolves, we're in Docker and should use it
    can_resolve_db = False
    try:
        socket.gethostbyname("db")
        can_resolve_db = True
    except (socket.gaierror, OSError):
        pass
    
    # Also check other Docker indicators
    has_dockerenv = os.path.exists("/.dockerenv")
    hostname = socket.gethostname()
    is_container_hostname = hostname in ["herald", "herald-db"]
    
    # If we can resolve 'db' OR we're clearly in Docker, use 'db' as hostname
    is_inside_docker = can_resolve_db or has_dockerenv or is_container_hostname
    
    print(f"Debug: DATABASE_URL (before): {database_url.split('@')[0]}@***")
    print(f"Debug: can_resolve_db: {can_resolve_db}, has_dockerenv: {has_dockerenv}, hostname: {hostname}")
    print(f"Debug: is_inside_docker: {is_inside_docker}")
    
    # Adjust database URL: Inside Docker always use 'db', on host use 'localhost'
    if database_url:
        if is_inside_docker:
            # Inside Docker: replace any localhost/127.0.0.1 with db
            if re.search(r'@(localhost|127\.0\.0\.1):', database_url):
                print("Note: Running inside Docker, replacing 'localhost'/'127.0.0.1' with 'db'")
                database_url = re.sub(r'@(localhost|127\.0\.0\.1):', r'@db:', database_url)
            # Also ensure 'db' is used if URL already has it
            elif "@db:" not in database_url:
                # If URL doesn't have db, replace whatever hostname with db
                database_url = re.sub(r'@[^:]+:', r'@db:', database_url)
                print(f"Note: Running inside Docker, forcing hostname to 'db'")
            print(f"Using: {database_url.split('@')[0]}@db:5432/herald")
        else:
            # On host: replace db with localhost if needed
            if "@db:" in database_url:
                print("Note: Running on host machine, replacing 'db' with 'localhost'")
                database_url = database_url.replace("@db:", "@localhost:")
                print(f"Using: {database_url.split('@')[0]}@localhost:5432/herald")
    
    print(f"Debug: Final DATABASE_URL: {database_url.split('@')[0]}@***")
    
    engine = create_async_engine(database_url, echo=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Check if column already exists
            check_column = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='games' AND column_name='last_activity_at'
            """)
            result = await session.execute(check_column)
            column_exists = result.scalar_one_or_none() is not None
            
            if column_exists:
                print("Column 'last_activity_at' already exists in 'games' table. Skipping column migration.")
            else:
                # Add last_activity_at column
                await session.execute(text("""
                    ALTER TABLE games 
                    ADD COLUMN last_activity_at TIMESTAMP WITH TIME ZONE
                """))
                
                # Create index for faster expiration queries
                await session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_games_last_activity_at 
                    ON games(last_activity_at)
                """))
                
                # Set last_activity_at to updated_at for existing games
                await session.execute(text("""
                    UPDATE games 
                    SET last_activity_at = updated_at 
                    WHERE last_activity_at IS NULL
                """))
                
                print("✓ Successfully added 'last_activity_at' column to 'games' table")
            
            # Check if EXPIRED enum value exists
            check_enum = text("""
                SELECT 1 
                FROM pg_enum 
                WHERE enumlabel = 'expired' 
                AND enumtypid = (
                    SELECT oid FROM pg_type WHERE typname = 'gamestatus'
                )
            """)
            result = await session.execute(check_enum)
            enum_exists = result.scalar_one_or_none() is not None
            
            if enum_exists:
                print("Enum value 'expired' already exists in 'gamestatus' enum. Skipping enum migration.")
            else:
                # Add EXPIRED value to GameStatus enum
                await session.execute(text("""
                    ALTER TYPE gamestatus ADD VALUE IF NOT EXISTS 'expired'
                """))
                print("✓ Successfully added 'expired' value to 'gamestatus' enum")
            
            await session.commit()
            print("✓ Migration completed successfully")
            
        except Exception as e:
            await session.rollback()
            print(f"✗ Migration failed: {e}")
            raise
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
