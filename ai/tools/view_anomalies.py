#!/usr/bin/env python3
"""
Script to view and query anomalies stored in the PostgreSQL database.
"""

import psycopg2
import psycopg2.extras
import argparse
import json
import logging
import tabulate
import datetime
import sys
from tools.db_utils import get_postgres_connection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_connection(host, port, user, password, dbname):
    """
    Connect to the PostgreSQL database.
    """
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

def list_anomalies(conn, limit=10, only_out_of_bounds=False, date_range=None, group_filter=None):
    """
    List anomalies from the database with optional filtering.
    """
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Build the query with various filters
        query = "SELECT id, group_value, comparison_mean, recent_mean, difference, std_dev, out_of_bounds, created_at FROM anomalies WHERE 1=1"
        params = []
        
        # Filter for out-of-bounds anomalies only
        if only_out_of_bounds:
            query += " AND out_of_bounds = %s"
            params.append(True)
            
        # Filter by date range
        if date_range:
            start_date, end_date = date_range
            if start_date:
                query += " AND created_at >= %s"
                params.append(start_date)
            if end_date:
                query += " AND created_at <= %s"
                params.append(end_date)
                
        # Filter by group value
        if group_filter:
            query += " AND group_value ILIKE %s"
            params.append(f"%{group_filter}%")
            
        # Add order and limit
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        
        cursor.execute(query, params)
        anomalies = cursor.fetchall()
        
        if not anomalies:
            logger.info("No anomalies found matching the criteria.")
            return []
            
        # Transform the data for display
        headers = ["ID", "Group", "Comp. Mean", "Recent Mean", "Difference", "Std Dev", "Anomaly?", "Created At"]
        rows = []
        
        for anomaly in anomalies:
            rows.append([
                anomaly['id'],
                anomaly['group_value'],
                f"{anomaly['comparison_mean']:.2f}",
                f"{anomaly['recent_mean']:.2f}",
                f"{anomaly['difference']:.2f}",
                f"{anomaly['std_dev']:.2f}",
                "✓" if anomaly['out_of_bounds'] else "✗",
                anomaly['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            ])
            
        return {"headers": headers, "rows": rows, "count": len(anomalies)}
    except Exception as e:
        logger.error(f"Error listing anomalies: {e}")
        return None
    finally:
        cursor.close()

def view_anomaly_details(conn, anomaly_id):
    """
    View detailed information about a specific anomaly.
    """
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM anomalies WHERE id = %s", (anomaly_id,))
        anomaly = cursor.fetchone()
        
        if not anomaly:
            logger.error(f"Anomaly with ID {anomaly_id} not found.")
            return None
            
        # Print basic information
        print(f"\nANOMALY DETAILS (ID: {anomaly['id']})")
        print(f"Group Value: {anomaly['group_value']}")
        print(f"Comparison Mean: {anomaly['comparison_mean']:.2f}")
        print(f"Recent Mean: {anomaly['recent_mean']:.2f}")
        print(f"Difference: {anomaly['difference']:.2f}")
        print(f"Standard Deviation: {anomaly['std_dev']:.2f}")
        print(f"Out of Bounds: {'Yes' if anomaly['out_of_bounds'] else 'No'}")
        print(f"Created At: {anomaly['created_at'].strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Print dates and counts
        print("\nTime Series Data:")
        dates = anomaly['dates']
        counts = anomaly['counts']
        
        data = []
        for i in range(len(dates)):
            data.append([dates[i], counts[i]])
        
        print(tabulate.tabulate(data, headers=["Date", "Value"], tablefmt="grid"))
        
        # Print metadata
        print("\nMetadata:")
        metadata = anomaly['metadata']
        for key, value in metadata.items():
            if isinstance(value, dict):
                print(f"  {key}:")
                for k, v in value.items():
                    print(f"    {k}: {v}")
            else:
                print(f"  {key}: {value}")
                
        return anomaly
    except Exception as e:
        logger.error(f"Error viewing anomaly details: {e}")
        return None
    finally:
        cursor.close()

def export_anomalies(conn, output_file, only_out_of_bounds=False, date_range=None, group_filter=None):
    """
    Export anomalies to a JSON file.
    """
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Build the query with various filters
        query = "SELECT * FROM anomalies WHERE 1=1"
        params = []
        
        # Filter for out-of-bounds anomalies only
        if only_out_of_bounds:
            query += " AND out_of_bounds = %s"
            params.append(True)
            
        # Filter by date range
        if date_range:
            start_date, end_date = date_range
            if start_date:
                query += " AND created_at >= %s"
                params.append(start_date)
            if end_date:
                query += " AND created_at <= %s"
                params.append(end_date)
                
        # Filter by group value
        if group_filter:
            query += " AND group_value ILIKE %s"
            params.append(f"%{group_filter}%")
            
        query += " ORDER BY created_at DESC"
        
        cursor.execute(query, params)
        anomalies = cursor.fetchall()
        
        if not anomalies:
            logger.info("No anomalies found matching the criteria.")
            return 0
        
        # Convert to JSON-serializable format
        result = []
        for anomaly in anomalies:
            item = dict(anomaly)
            # Convert datetime objects to strings
            item['created_at'] = item['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            result.append(item)
            
        # Write to file
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
            
        logger.info(f"Exported {len(result)} anomalies to {output_file}")
        return len(result)
    except Exception as e:
        logger.error(f"Error exporting anomalies: {e}")
        return 0
    finally:
        cursor.close()

def main():
    parser = argparse.ArgumentParser(description="View anomalies stored in the PostgreSQL database")
    parser.add_argument("--host", default="localhost", help="PostgreSQL host (default: localhost)")
    parser.add_argument("--port", type=int, default=5432, help="PostgreSQL port (default: 5432)")
    parser.add_argument("--user", default="postgres", help="PostgreSQL user (default: postgres)")
    parser.add_argument("--password", default="postgres", help="PostgreSQL password (default: postgres)")
    parser.add_argument("--dbname", default="transparentsf", help="Database name (default: transparentsf)")
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List anomalies')
    list_parser.add_argument("--limit", type=int, default=10, help="Maximum number of anomalies to list (default: 10)")
    list_parser.add_argument("--anomalies-only", action="store_true", help="Show only out-of-bounds anomalies")
    list_parser.add_argument("--start-date", help="Filter by start date (YYYY-MM-DD)")
    list_parser.add_argument("--end-date", help="Filter by end date (YYYY-MM-DD)")
    list_parser.add_argument("--group", help="Filter by group value (case-insensitive substring match)")
    
    # Details command
    details_parser = subparsers.add_parser('details', help='View details of a specific anomaly')
    details_parser.add_argument("id", type=int, help="ID of the anomaly to view")
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export anomalies to a JSON file')
    export_parser.add_argument("output", help="Output file path")
    export_parser.add_argument("--anomalies-only", action="store_true", help="Export only out-of-bounds anomalies")
    export_parser.add_argument("--start-date", help="Filter by start date (YYYY-MM-DD)")
    export_parser.add_argument("--end-date", help="Filter by end date (YYYY-MM-DD)")
    export_parser.add_argument("--group", help="Filter by group value (case-insensitive substring match)")
    
    args = parser.parse_args()
    
    # Connect to the database
    conn = get_connection(args.host, args.port, args.user, args.password, args.dbname)
    if not conn:
        logger.error("Failed to connect to the database. Exiting.")
        sys.exit(1)
    
    try:
        if args.command == 'list':
            # Parse date range if provided
            date_range = None
            if args.start_date or args.end_date:
                start_date = datetime.datetime.strptime(args.start_date, '%Y-%m-%d') if args.start_date else None
                end_date = datetime.datetime.strptime(args.end_date, '%Y-%m-%d') if args.end_date else None
                date_range = (start_date, end_date)
            
            result = list_anomalies(
                conn, 
                limit=args.limit, 
                only_out_of_bounds=args.anomalies_only,
                date_range=date_range,
                group_filter=args.group
            )
            
            if result:
                print(f"\nFound {result['count']} anomalies:")
                print(tabulate.tabulate(result['rows'], headers=result['headers'], tablefmt="grid"))
        
        elif args.command == 'details':
            view_anomaly_details(conn, args.id)
            
        elif args.command == 'export':
            # Parse date range if provided
            date_range = None
            if args.start_date or args.end_date:
                start_date = datetime.datetime.strptime(args.start_date, '%Y-%m-%d') if args.start_date else None
                end_date = datetime.datetime.strptime(args.end_date, '%Y-%m-%d') if args.end_date else None
                date_range = (start_date, end_date)
            
            export_anomalies(
                conn,
                args.output,
                only_out_of_bounds=args.anomalies_only,
                date_range=date_range,
                group_filter=args.group
            )
            
        else:
            parser.print_help()
    
    finally:
        conn.close()

if __name__ == "__main__":
    main() 