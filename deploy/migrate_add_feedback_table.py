"""Migration: Add feedback table."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def main():
    """Run migration to add feedback table."""
    # Load DATABASE_URL from environment or .env file
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
                            # Skip comments and empty lines
                            if not line or line.startswith("#"):
                                continue
                            if line.startswith("DATABASE_URL="):
                                # Split on first = only
                                database_url = line.split("=", 1)[1].strip()
                                # Remove surrounding quotes if present
                                if database_url.startswith('"') and database_url.endswith('"'):
                                    database_url = database_url[1:-1]
                                elif database_url.startswith("'") and database_url.endswith("'"):
                                    database_url = database_url[1:-1]
                                print(f"✓ Loaded DATABASE_URL from .env file: {env_file}")
                                print(f"  DATABASE_URL length: {len(database_url)}")
                                break
                    if database_url:
                        break
                except Exception as e:
                    print(f"Warning: Could not read .env file at {env_file}: {e}")
        
        if not database_url:
            print(f"Warning: .env file not found. Tried:")
            for loc in possible_locations:
                print(f"  - {loc} (exists: {loc.exists()}, is_file: {loc.is_file() if loc.exists() else False})")
    
    if not database_url:
        database_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
        print("WARNING: Using default DATABASE_URL")
    else:
        # Validate DATABASE_URL is not empty
        if not database_url or database_url.strip() == "":
            print("ERROR: DATABASE_URL is empty!")
            database_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
            print("WARNING: Using default DATABASE_URL")
    
    # Debug: show what we're using (safely)
    print(f"Using DATABASE_URL (length: {len(database_url)})")
    if database_url.startswith("postgresql"):
        # Show connection details without password
        parts = database_url.split("@")
        if len(parts) == 2:
            conn_part = parts[0].split('//')[0]
            host_part = parts[1].split('/')[0]  # host:port
            db_part = parts[1].split('/')[1] if '/' in parts[1] else 'unknown'
            print(f"  Connection: {conn_part}//***@{host_part}/{db_part}")
            # Extract hostname for DNS check
            hostname = host_part.split(':')[0]
            print(f"  Hostname: {hostname}")
            print(f"  Database: {db_part}")
        else:
            print(f"  Connection: {database_url[:50]}...")
    else:
        print(f"  Connection: {database_url[:50]}...")
    
    print(f"Connecting to database...")
    try:
        engine = create_async_engine(database_url, echo=False)
        print("✓ Database engine created successfully")
    except Exception as e:
        print(f"ERROR: Failed to create database engine: {e}")
        print(f"  DATABASE_URL value: {repr(database_url)}")
        raise
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    print("Attempting to establish database connection...")
    try:
        async with async_session() as session:
            try:
                # Check if feedback table already exists
                check_stmt = text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = 'feedback'
                    );
                """)
                result = await session.execute(check_stmt)
                exists = result.scalar()
                
                if exists:
                    print("✓ Feedback table already exists, skipping migration")
                    return
                
                print("Creating feedback table...")
                
                # Create feedback table
                create_table_stmt = text("""
                    CREATE TABLE feedback (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        name VARCHAR(200) NOT NULL,
                        email VARCHAR(200) NOT NULL,
                        message TEXT NOT NULL,
                        read BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                    );
                """)
                
                await session.execute(create_table_stmt)
                
                # Create index on read status for faster queries
                create_index_stmt = text("""
                    CREATE INDEX IF NOT EXISTS ix_feedback_read ON feedback(read);
                """)
                await session.execute(create_index_stmt)
                
                # Create index on created_at for sorting
                create_index_stmt2 = text("""
                    CREATE INDEX IF NOT EXISTS ix_feedback_created_at ON feedback(created_at DESC);
                """)
                await session.execute(create_index_stmt2)
                
                await session.commit()
                print("✓ Feedback table created successfully")
            except Exception as e:
                await session.rollback()
                print(f"✗ Error during migration: {e}")
                import traceback
                print("Full traceback:")
                traceback.print_exc()
                raise
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}")
        import traceback
        print("Full traceback:")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())
