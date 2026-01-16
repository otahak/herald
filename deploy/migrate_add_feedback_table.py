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
        # Try loading from .env file
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    if line.strip().startswith("DATABASE_URL="):
                        database_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
    
    if not database_url:
        database_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/herald"
        print("WARNING: Using default DATABASE_URL")
    
    print(f"Connecting to database...")
    engine = create_async_engine(database_url, echo=False)
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
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
            print(f"✗ Error: {e}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
