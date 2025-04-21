#!/usr/bin/env python3
"""
Script to drop the TransparentSF database.
"""

import psycopg2
import logging
import argparse
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def drop_database(host, port, user, password, dbname):
    """
    Drop the PostgreSQL database if it exists.
    """
    try:
        # Connect to the default 'postgres' database to drop our database
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname='postgres'
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Check if database exists
        cursor.execute(f"SELECT 1 FROM pg_database WHERE datname = '{dbname}'")
        exists = cursor.fetchone()
        
        if exists:
            logger.info(f"Dropping database '{dbname}'...")
            cursor.execute(f"DROP DATABASE {dbname}")
            logger.info(f"Database '{dbname}' dropped successfully.")
        else:
            logger.info(f"Database '{dbname}' does not exist.")
            
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to drop database: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Drop PostgreSQL database for TransparentSF")
    parser.add_argument("--host", default="localhost", help="PostgreSQL host (default: localhost)")
    parser.add_argument("--port", type=int, default=5432, help="PostgreSQL port (default: 5432)")
    parser.add_argument("--user", default="postgres", help="PostgreSQL user (default: postgres)")
    parser.add_argument("--password", default="postgres", help="PostgreSQL password (default: postgres)")
    parser.add_argument("--dbname", default="transparentsf", help="Database name (default: transparentsf)")
    
    args = parser.parse_args()
    
    logger.info(f"Dropping PostgreSQL database '{args.dbname}' on {args.host}:{args.port}")
    
    if not drop_database(args.host, args.port, args.user, args.password, args.dbname):
        logger.error("Database drop failed. Exiting.")
        sys.exit(1)
    
    logger.info("Database drop completed successfully.")

if __name__ == "__main__":
    main() 