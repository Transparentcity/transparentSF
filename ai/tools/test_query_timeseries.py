#!/usr/bin/env python3
import sys
import os
import logging
import pandas as pd
from pathlib import Path

# Add the parent directory to sys.path to import modules
parent_dir = str(Path(__file__).resolve().parent.parent.parent)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from ai.tools.store_time_series import get_conn, get_time_series_metadata, get_time_series_data, get_biggest_deltas

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    """Query the time series database and display results."""
    conn = get_conn()
    logger.info("Successfully connected to PostgreSQL database.")
    
    # Get all time series metadata
    try:
        query = "SELECT * FROM time_series_metadata ORDER BY chart_id DESC LIMIT 10"
        df_metadata = pd.read_sql(query, conn)
        logger.info(f"Found {len(df_metadata)} time series metadata records:")
        print("\nTime Series Metadata:")
        print(df_metadata)
        
        # For the most recent chart, get the time series data
        if not df_metadata.empty:
            chart_id = df_metadata.iloc[0]['chart_id']
            logger.info(f"Querying time series data for chart ID: {chart_id}")
            
            df_timeseries = get_time_series_data(chart_id)
            logger.info(f"Found {len(df_timeseries)} time series data points:")
            print("\nTime Series Data Sample:")
            print(df_timeseries.head(10))
            
            # Get biggest deltas between two periods
            current_period = "2023-12"
            comparison_period = "2023-11"
            logger.info(f"Querying for biggest deltas between {comparison_period} and {current_period}")
            
            deltas = get_biggest_deltas(current_period, comparison_period, limit=5)
            logger.info(f"Found {len(deltas['results']) if 'results' in deltas else 0} deltas:")
            print("\nBiggest Deltas:")
            print(deltas)
        
    except Exception as e:
        logger.error(f"Error querying database: {e}")
    finally:
        conn.close()
        logger.info("Database connection closed.")

if __name__ == "__main__":
    main() 