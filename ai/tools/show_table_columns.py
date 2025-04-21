#!/usr/bin/env python3
"""
Script to show columns in the anomalies table in the TransparentSF database.
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

def show_table_columns(host, port, user, password, dbname, table_name):
    """
    Show columns in the specified table.
    """
    try:
        # Connect to the database
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname
        )
        cursor = conn.cursor()
        
        # Query to get column information
        cursor.execute(f"""
            SELECT column_name, data_type, character_maximum_length, 
                   is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = '{table_name}'
            ORDER BY ordinal_position
        """)
        
        columns = cursor.fetchall()
        
        if columns:
            logger.info(f"Columns in table '{table_name}':")
            print("\n{:<30} {:<15} {:<10} {:<10} {:<30}".format(
                "Column Name", "Data Type", "Max Length", "Nullable", "Default"
            ))
            print("-" * 95)
            
            for col in columns:
                print("{:<30} {:<15} {:<10} {:<10} {:<30}".format(
                    col[0], col[1], col[2] or "", col[3], col[4] or ""
                ))
        else:
            logger.info(f"Table '{table_name}' does not exist or has no columns.")
            
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to show table columns: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Show columns in a PostgreSQL table for TransparentSF")
    parser.add_argument("--host", default="localhost", help="PostgreSQL host (default: localhost)")
    parser.add_argument("--port", type=int, default=5432, help="PostgreSQL port (default: 5432)")
    parser.add_argument("--user", default="postgres", help="PostgreSQL user (default: postgres)")
    parser.add_argument("--password", default="postgres", help="PostgreSQL password (default: postgres)")
    parser.add_argument("--dbname", default="transparentsf", help="Database name (default: transparentsf)")
    parser.add_argument("--table", default="anomalies", help="Table name (default: anomalies)")
    
    args = parser.parse_args()
    
    logger.info(f"Showing columns in table '{args.table}' in database '{args.dbname}' on {args.host}:{args.port}")
    
    if not show_table_columns(args.host, args.port, args.user, args.password, args.dbname, args.table):
        logger.error("Failed to show table columns. Exiting.")
        sys.exit(1)
    
    logger.info("Operation completed successfully.")

if __name__ == "__main__":
    main() 