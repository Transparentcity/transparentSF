#!/usr/bin/env python3
"""
Database migration script to add session tracking fields to anomalies and time_series_metadata tables.
This allows tracking of analysis sessions for metrics and anomalies.

Design Decision: Dedicated Columns vs JSONB Metadata
==================================================
We use dedicated columns (analysis_session_id, analysis_date, analysis_status) instead of storing
this data in the existing metadata JSONB field because:

1. Query Performance: These fields will be frequently queried and filtered on
2. Indexing: PostgreSQL can create efficient B-tree indexes on dedicated columns
3. Data Integrity: Can add constraints (CHECK, NOT NULL) for data validation
4. Query Simplicity: Easier to write and maintain queries for session status
5. Type Safety: Proper TIMESTAMP and TEXT types with constraints

The analysis_response_text will be stored in the metadata JSONB field to keep it flexible
while maintaining performance for the core session tracking queries.
"""

import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

# Add the parent directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.db_utils import get_postgres_connection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def add_session_tracking_fields():
    """Add session tracking fields to anomalies and time_series_metadata tables."""
    
    try:
        with get_postgres_connection() as conn:
            with conn.cursor() as cursor:
                logger.info("Starting database migration to add session tracking fields...")
                
                # Add session tracking fields to anomalies table
                logger.info("Adding session tracking fields to anomalies table...")
                cursor.execute("""
                    ALTER TABLE anomalies 
                    ADD COLUMN IF NOT EXISTS analysis_session_id TEXT,
                    ADD COLUMN IF NOT EXISTS analysis_date TIMESTAMP,
                    ADD COLUMN IF NOT EXISTS analysis_status TEXT DEFAULT 'pending' CHECK (analysis_status IN ('pending', 'completed', 'failed'))
                """)
                
                # Add indexes for the new fields
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS anomalies_analysis_session_id_idx 
                    ON anomalies (analysis_session_id);
                    CREATE INDEX IF NOT EXISTS anomalies_analysis_date_idx 
                    ON anomalies (analysis_date);
                    CREATE INDEX IF NOT EXISTS anomalies_analysis_status_idx 
                    ON anomalies (analysis_status);
                """)
                
                # Add session tracking fields to time_series_metadata table
                logger.info("Adding session tracking fields to time_series_metadata table...")
                cursor.execute("""
                    ALTER TABLE time_series_metadata 
                    ADD COLUMN IF NOT EXISTS analysis_session_id TEXT,
                    ADD COLUMN IF NOT EXISTS analysis_date TIMESTAMP,
                    ADD COLUMN IF NOT EXISTS analysis_status TEXT DEFAULT 'pending' CHECK (analysis_status IN ('pending', 'completed', 'failed'))
                """)
                
                # Add indexes for the new fields
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS time_series_metadata_analysis_session_id_idx 
                    ON time_series_metadata (analysis_session_id);
                    CREATE INDEX IF NOT EXISTS time_series_metadata_analysis_date_idx 
                    ON time_series_metadata (analysis_date);
                    CREATE INDEX IF NOT EXISTS time_series_metadata_analysis_status_idx 
                    ON time_series_metadata (analysis_status);
                """)
                
                # Commit the changes
                conn.commit()
                logger.info("Successfully added session tracking fields to both tables")
                
                # Verify the changes
                cursor.execute("""
                    SELECT column_name, data_type, is_nullable 
                    FROM information_schema.columns 
                    WHERE table_name = 'anomalies' 
                    AND column_name IN ('analysis_session_id', 'analysis_date', 'analysis_status')
                    ORDER BY column_name;
                """)
                anomalies_columns = cursor.fetchall()
                logger.info(f"Anomalies table new columns: {anomalies_columns}")
                
                cursor.execute("""
                    SELECT column_name, data_type, is_nullable 
                    FROM information_schema.columns 
                    WHERE table_name = 'time_series_metadata' 
                    AND column_name IN ('analysis_session_id', 'analysis_date', 'analysis_status')
                    ORDER BY column_name;
                """)
                metadata_columns = cursor.fetchall()
                logger.info(f"Time series metadata table new columns: {metadata_columns}")
                
                return True
                
    except Exception as e:
        logger.error(f"Error adding session tracking fields: {str(e)}")
        return False

def rollback_session_tracking_fields():
    """Rollback the session tracking fields (for testing purposes)."""
    
    try:
        with get_postgres_connection() as conn:
            with conn.cursor() as cursor:
                logger.info("Rolling back session tracking fields...")
                
                # Drop indexes first
                cursor.execute("DROP INDEX IF EXISTS anomalies_analysis_session_id_idx;")
                cursor.execute("DROP INDEX IF EXISTS anomalies_analysis_date_idx;")
                cursor.execute("DROP INDEX IF EXISTS anomalies_analysis_status_idx;")
                cursor.execute("DROP INDEX IF EXISTS time_series_metadata_analysis_session_id_idx;")
                cursor.execute("DROP INDEX IF EXISTS time_series_metadata_analysis_date_idx;")
                cursor.execute("DROP INDEX IF EXISTS time_series_metadata_analysis_status_idx;")
                
                # Drop columns
                cursor.execute("ALTER TABLE anomalies DROP COLUMN IF EXISTS analysis_session_id;")
                cursor.execute("ALTER TABLE anomalies DROP COLUMN IF EXISTS analysis_date;")
                cursor.execute("ALTER TABLE anomalies DROP COLUMN IF EXISTS analysis_status;")
                
                cursor.execute("ALTER TABLE time_series_metadata DROP COLUMN IF EXISTS analysis_session_id;")
                cursor.execute("ALTER TABLE time_series_metadata DROP COLUMN IF EXISTS analysis_date;")
                cursor.execute("ALTER TABLE time_series_metadata DROP COLUMN IF EXISTS analysis_status;")
                
                conn.commit()
                logger.info("Successfully rolled back session tracking fields")
                return True
                
    except Exception as e:
        logger.error(f"Error rolling back session tracking fields: {str(e)}")
        return False

if __name__ == "__main__":
    load_dotenv()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        success = rollback_session_tracking_fields()
    else:
        success = add_session_tracking_fields()
    
    if success:
        print("Migration completed successfully!")
        sys.exit(0)
    else:
        print("Migration failed!")
        sys.exit(1)
