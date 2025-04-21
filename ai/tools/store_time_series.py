#!/usr/bin/env python3
"""
Functions to store time series chart data in PostgreSQL database.
"""

import os
import json
import psycopg2
import psycopg2.extras
import logging
import pandas as pd
from typing import Dict, List, Any, Optional, Union, Tuple
from datetime import datetime
from tools.db_utils import get_postgres_connection, execute_with_connection, CustomJSONEncoder
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def extract_district_from_filter_conditions(filter_conditions: List[Dict[str, Any]]) -> int:
    """
    Extract district value from filter conditions.
    
    Args:
        filter_conditions: List of filter condition dictionaries
        
    Returns:
        int: District value, or 0 if not found
    """
    district = None
    if filter_conditions:
        for condition in filter_conditions:
            field = condition.get('field', '').lower()
            if field in ['district', 'police_district', 'supervisor_district']:
                district = condition.get('value')
                break
    
    # Default district to 0 if it's null
    if district is None:
        district = 0
        logging.info("Setting null district to default value 0")
    else:
        # Try to convert district to integer
        try:
            district = int(district)
        except (ValueError, TypeError):
            logging.warning(f"Non-numeric district value '{district}' converted to 0")
            district = 0
    
    return district

def store_time_series_in_db(connection, chart_data, metadata):
    """
    Store time series chart data in the PostgreSQL database.
    
    Args:
        connection: PostgreSQL database connection
        chart_data: List of data points for the chart
        metadata: Metadata about the chart
    
    Returns:
        int: Number of data points stored
    """
    if connection is None:
        logging.error("No database connection available")
        return 0
    
    try:
        # Start a transaction
        connection.autocommit = False
        
        # Use the custom JSON encoder to serialize the entire metadata
        # This handles all nested date objects automatically
        serializable_metadata = json.loads(json.dumps(metadata, cls=CustomJSONEncoder))
        
        # Extract object information from metadata
        object_type = serializable_metadata.get('object_type', 'unknown')
        object_id = serializable_metadata.get('object_id', 'unknown')
        object_name = serializable_metadata.get('object_name', serializable_metadata.get('title', 'unknown'))
        field_name = serializable_metadata.get('field_name', 'unknown')
        period_type = serializable_metadata.get('period_type', 'month')
        group_field = serializable_metadata.get('group_field', None)
        executed_query_url = serializable_metadata.get('executed_query_url', None)
        caption = serializable_metadata.get('caption', '')
        
        # Get district from filter conditions if it exists
        district = extract_district_from_filter_conditions(serializable_metadata.get('filter_conditions', []))
        
        # First, insert the metadata to get a chart_id
        chart_id = None
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO time_series_metadata (
                    object_type, object_id, object_name, field_name, period_type,
                    group_field, executed_query_url, caption, metadata, district
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING chart_id
            """, (
                object_type, object_id, object_name, field_name, period_type,
                group_field, executed_query_url, caption, Json(serializable_metadata), district
            ))
            chart_id = cursor.fetchone()[0]
        
        # Then insert each data point
        inserted_count = 0
        with connection.cursor() as cursor:
            for point in chart_data:
                cursor.execute("""
                    INSERT INTO time_series_data (
                        chart_id, time_period, value, group_value
                    ) VALUES (%s, %s, %s, %s)
                """, (
                    chart_id,
                    point['time_period'],
                    point['value'],
                    point.get('group_value')
                ))
                inserted_count += 1
        
        connection.commit()
        return inserted_count
    except Exception as e:
        logging.error(f"Error storing time series data in database: {e}")
        connection.rollback()
        return 0

def get_biggest_deltas(
    current_period: str = None,
    comparison_period: str = None,
    limit: int = 10,
    district: Optional[int] = None,
    object_type: Optional[str] = None,
    db_host=None,
    db_port=None,
    db_name=None,
    db_user=None,
    db_password=None
) -> Dict[str, Any]:
    """
    Get the biggest deltas between two time periods.
    
    Args:
        current_period: Current time period to compare
        comparison_period: Previous time period to compare against
        limit: Maximum number of results to return
        district: Optional district filter
        object_type: Optional object type filter
        db_host: Database host
        db_port: Database port
        db_name: Database name
        db_user: Database user
        db_password: Database password
        
    Returns:
        dict: Query results with deltas
    """
    def query_operation(connection):
        # Create cursor with dictionary-like results
        cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Build query
        query = """
            WITH current_data AS (
                SELECT 
                    m.chart_id,
                    m.object_name,
                    m.field_name,
                    m.period_type,
                    m.district,
                    m.object_type,
                    d.value as current_value,
                    d.time_period as current_period
                FROM time_series_metadata m
                JOIN time_series_data d ON m.chart_id = d.chart_id
                WHERE d.time_period = %s
            ),
            comparison_data AS (
                SELECT 
                    m.chart_id,
                    d.value as comparison_value,
                    d.time_period as comparison_period
                FROM time_series_metadata m
                JOIN time_series_data d ON m.chart_id = d.chart_id
                WHERE d.time_period = %s
            )
            SELECT 
                c.chart_id,
                c.object_name,
                c.field_name,
                c.period_type,
                c.district,
                c.object_type,
                c.current_value,
                c.current_period,
                p.comparison_value,
                p.comparison_period,
                (c.current_value - p.comparison_value) as delta,
                CASE 
                    WHEN p.comparison_value = 0 THEN NULL
                    ELSE ((c.current_value - p.comparison_value) / p.comparison_value) * 100 
                END as percent_change
            FROM current_data c
            JOIN comparison_data p ON c.chart_id = p.chart_id
            WHERE 1=1
        """
        
        params = [current_period, comparison_period]
        
        if district is not None:
            query += " AND c.district = %s"
            params.append(district)
            
        if object_type:
            query += " AND c.object_type = %s"
            params.append(object_type)
            
        query += """
            ORDER BY ABS(delta) DESC
            LIMIT %s
        """
        params.append(limit)
        
        # Execute query
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        # Convert to list of dictionaries and handle datetime objects
        result_list = []
        for row in results:
            row_dict = dict(row)
            result_list.append(row_dict)
        
        cursor.close()
        return result_list
    
    result = execute_with_connection(
        operation=query_operation,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password
    )
    
    if result["status"] == "success":
        return {
            "status": "success",
            "count": len(result["result"]),
            "results": result["result"]
        }
    else:
        return result

# Add aliases and additional helper functions for the test script
def get_conn(host=None, port=None, dbname=None, user=None, password=None):
    """
    Alias for get_postgres_connection for backward compatibility.
    """
    return get_postgres_connection(host, port, dbname, user, password)

def get_time_series_metadata(chart_id=None, db_host=None, db_port=None, db_name=None, db_user=None, db_password=None):
    """
    Get metadata for time series charts.
    
    Args:
        chart_id: Optional chart ID to filter by
        db_host: Database host
        db_port: Database port
        db_name: Database name
        db_user: Database user
        db_password: Database password
        
    Returns:
        DataFrame: Chart metadata records
    """
    try:
        # Connect to database
        connection = get_postgres_connection(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=db_user,
            password=db_password
        )
        
        if connection is None:
            logging.error("Failed to connect to database")
            return pd.DataFrame()
        
        # Build query
        query = "SELECT * FROM time_series_metadata"
        params = []
        
        if chart_id is not None:
            query += " WHERE chart_id = %s"
            params.append(chart_id)
            
        query += " ORDER BY created_at DESC"
        
        # Execute query
        df = pd.read_sql(query, connection, params=params)
        connection.close()
        
        return df
    
    except Exception as e:
        logging.error(f"Error getting time series metadata: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return pd.DataFrame()

def get_time_series_data(chart_id, db_host=None, db_port=None, db_name=None, db_user=None, db_password=None):
    """
    Get time series data points for a specific chart.
    
    Args:
        chart_id: Chart ID to get data for
        db_host: Database host
        db_port: Database port
        db_name: Database name
        db_user: Database user
        db_password: Database password
        
    Returns:
        DataFrame: Time series data points
    """
    try:
        # Connect to database
        connection = get_postgres_connection(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=db_user,
            password=db_password
        )
        
        if connection is None:
            logging.error("Failed to connect to database")
            return pd.DataFrame()
        
        # Build query
        query = """
            SELECT ts.*, tm.object_name, tm.field_name, tm.y_axis_label, tm.period_type
            FROM time_series_data ts
            JOIN time_series_metadata tm ON ts.chart_id = tm.chart_id
            WHERE ts.chart_id = %s
            ORDER BY ts.time_period
        """
        
        # Execute query
        df = pd.read_sql(query, connection, params=[chart_id])
        connection.close()
        
        return df
    
    except Exception as e:
        logging.error(f"Error getting time series data: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return pd.DataFrame() 