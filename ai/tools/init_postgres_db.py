#!/usr/bin/env python3
"""
PostgreSQL database initialization script for TransparentSF anomaly detection.
This script creates the database and necessary tables if they don't exist.
"""

import os
import logging
from db_utils import get_postgres_connection, execute_with_connection

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_database():
    """
    Initialize the PostgreSQL database with required tables and schemas.
    """
    connection = None
    try:
        # Get database connection
        connection = get_postgres_connection()
        if not connection:
            logger.error("Failed to establish database connection")
            return False

        # Create tables
        cursor = connection.cursor()
        
        # Drop existing tables in reverse order of dependencies
        cursor.execute("""
            DROP TABLE IF EXISTS monthly_reporting CASCADE;
            DROP TABLE IF EXISTS reports CASCADE;
            DROP TABLE IF EXISTS charts CASCADE;
            DROP TABLE IF EXISTS anomalies CASCADE;
        """)

        # Create trigger function for updating timestamps
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ language 'plpgsql';
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS anomalies (
            id SERIAL PRIMARY KEY,
            group_value TEXT,
            group_field_name TEXT,  
            period_type TEXT,
            comparison_mean FLOAT,
            recent_mean FLOAT,
            difference FLOAT,
            std_dev FLOAT,
            out_of_bounds BOOLEAN,
            recent_date DATE,
            comparison_dates JSONB,
            comparison_counts JSONB,
            recent_dates JSONB,
            recent_counts JSONB,
            metadata JSONB,
            field_name TEXT,
            object_type TEXT,
            object_id TEXT,
            object_name TEXT,
            recent_data JSONB,
            comparison_data JSONB,
            district TEXT,
            executed_query_url TEXT,
            caption TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Create an index on the created_at column for faster querying
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS anomalies_created_at_idx ON anomalies (created_at)
        """)
        
        # Create an index on the out_of_bounds column for faster filtering
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS anomalies_out_of_bounds_idx ON anomalies (out_of_bounds)
        """)
        
        # Create an index on the district column for faster filtering
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS anomalies_district_idx ON anomalies (district)
        """)
        
        # Create time_series_metadata table for time series data
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS time_series_metadata (
                chart_id SERIAL PRIMARY KEY,
                object_type TEXT,
                object_id TEXT,
                object_name TEXT,
                field_name TEXT,
                y_axis_label TEXT,
                period_type TEXT,
                chart_title TEXT,
                filter_conditions JSONB,
                district INTEGER DEFAULT 0,
                group_field TEXT,
                executed_query_url TEXT,
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for time_series_metadata
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS time_series_metadata_object_type_id_idx ON time_series_metadata (object_type, object_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS time_series_metadata_field_name_idx ON time_series_metadata (field_name)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS time_series_metadata_district_idx ON time_series_metadata (district)
        """)
        
        # Create time_series_data table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS time_series_data (
                id SERIAL PRIMARY KEY,
                chart_id INTEGER REFERENCES time_series_metadata(chart_id),
                time_period DATE,
                group_value TEXT,
                numeric_value FLOAT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for time_series_data
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS time_series_data_chart_id_idx ON time_series_data (chart_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS time_series_data_time_period_idx ON time_series_data (time_period)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS time_series_data_group_value_idx ON time_series_data (group_value)
        """)
        
        # Create a composite index for common query patterns
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS time_series_data_chart_time_idx ON time_series_data (chart_id, time_period)
        """)
        
        cursor.execute("""
            CREATE TABLE reports (
                id SERIAL PRIMARY KEY,
                district VARCHAR(50) NOT NULL,
                period_type VARCHAR(20) NOT NULL,
                max_items INTEGER NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                revised_filename VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT valid_period_type CHECK (period_type IN ('month', 'quarter', 'year')),
                CONSTRAINT valid_max_items CHECK (max_items > 0)
            )
        """)

        # Create monthly_reporting table (child table)
        cursor.execute("""
            CREATE TABLE monthly_reporting (
                id SERIAL PRIMARY KEY,
                report_id INTEGER NOT NULL,
                item_title VARCHAR(255) NOT NULL,
                explanation TEXT,
                report_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_report
                    FOREIGN KEY(report_id) 
                    REFERENCES reports(id)
                    ON DELETE CASCADE
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX idx_reports_district ON reports(district);
            CREATE INDEX idx_reports_period_type ON reports(period_type);
            CREATE INDEX idx_monthly_reporting_report_id ON monthly_reporting(report_id);
        """)

        # Create triggers for updating timestamps
        cursor.execute("""
            CREATE TRIGGER update_reports_updated_at
                BEFORE UPDATE ON reports
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column()
        """)

        cursor.execute("""
            CREATE TRIGGER update_monthly_reporting_updated_at
                BEFORE UPDATE ON monthly_reporting
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column()
        """)

        connection.commit()
        cursor.close()
        logger.info("Successfully initialized database tables")
        return True    

    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        if connection:
            connection.rollback()
        return False
    finally:
        if connection:
            connection.close()

if __name__ == "__main__":
    init_database() 