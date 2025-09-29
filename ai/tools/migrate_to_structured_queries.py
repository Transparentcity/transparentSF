"""
Migration script to add structured query configuration to existing metrics.

This script analyzes existing metrics and adds structured query configuration
to their metadata field, replacing the need for regex parsing.
"""

import os
import sys
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

# Add the parent directory to the path so we can import from ai.tools
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.database_connection import get_connection
from tools.query_config_schema import create_query_config
from tools.structured_query_parser import extract_date_field_regex_fallback, extract_aggregation_function_regex_fallback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def analyze_query_for_config(query: str, metric_name: str) -> Dict[str, Any]:
    """
    Analyze a query to determine its configuration parameters.
    
    Args:
        query: The SQL query to analyze
        metric_name: The name of the metric (for context)
        
    Returns:
        Dictionary containing the configuration parameters
    """
    config = {}
    
    # Extract date field using regex fallback
    date_field = extract_date_field_regex_fallback(query)
    if date_field:
        config['date_field'] = date_field
    else:
        logger.warning(f"Could not extract date field from query for {metric_name}")
        config['date_field'] = 'date'  # Default fallback
    
    # Extract aggregation function using regex fallback
    aggregation_func = extract_aggregation_function_regex_fallback(query)
    config['aggregation_function'] = aggregation_func
    
    # Determine trunc type based on query content
    if 'date_trunc_ymd' in query.lower():
        config['trunc_type'] = 'ymd'
    elif 'date_trunc_ym' in query.lower():
        config['trunc_type'] = 'ym'
    elif 'date_trunc_y' in query.lower():
        config['trunc_type'] = 'y'
    else:
        config['trunc_type'] = 'ymd'  # Default
    
    # Check if it's a fiscal year query
    config['is_fiscal_year'] = 'fiscal_year' in query.lower()
    
    # Check if it supports districts
    config['supports_districts'] = 'supervisor_district' in query.lower()
    
    # Check if it's a YTD query
    config['is_ytd_query'] = (
        'as date' in query.lower() or 
        'date_trunc' in query.lower() or
        'ytd' in metric_name.lower()
    )
    
    # Determine aggregation type and field
    if 'COUNT(*)' in aggregation_func:
        config['aggregation_type'] = 'COUNT'
        config['aggregation_field'] = None
    elif 'COUNT(DISTINCT' in aggregation_func:
        config['aggregation_type'] = 'COUNT'
        config['aggregation_field'] = aggregation_func.split('(')[1].split(')')[0].replace('DISTINCT ', '')
        config['distinct'] = True
    elif aggregation_func.startswith('SUM('):
        config['aggregation_type'] = 'SUM'
        config['aggregation_field'] = aggregation_func.split('(')[1].split(')')[0]
    elif aggregation_func.startswith('AVG('):
        config['aggregation_type'] = 'AVG'
        config['aggregation_field'] = aggregation_func.split('(')[1].split(')')[0]
    elif aggregation_func.startswith('MAX('):
        config['aggregation_type'] = 'MAX'
        config['aggregation_field'] = aggregation_func.split('(')[1].split(')')[0]
    elif aggregation_func.startswith('MIN('):
        config['aggregation_type'] = 'MIN'
        config['aggregation_field'] = aggregation_func.split('(')[1].split(')')[0]
    else:
        # Try to extract from the original query if regex failed
        if 'AVG(' in query:
            # Look for AVG patterns in the query
            import re
            avg_match = re.search(r'AVG\s*\(([^)]+)\)', query, re.IGNORECASE)
            if avg_match:
                config['aggregation_type'] = 'AVG'
                config['aggregation_field'] = avg_match.group(1).strip()
            else:
                config['aggregation_type'] = 'COUNT'
                config['aggregation_field'] = None
        else:
            config['aggregation_type'] = 'COUNT'
            config['aggregation_field'] = None
    
    return config

def create_structured_config_for_metric(metric: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Create structured query configuration for a metric.
    
    Args:
        metric: Dictionary containing metric information
        
    Returns:
        Structured configuration dictionary, or None if creation failed
    """
    try:
        # Get the YTD query (preferred) or metric query
        ytd_query = metric.get('ytd_query', '')
        metric_query = metric.get('metric_query', '')
        
        # Use YTD query if available, otherwise use metric query
        query_to_analyze = ytd_query if ytd_query else metric_query
        
        if not query_to_analyze:
            logger.warning(f"No query found for metric {metric.get('metric_name', 'Unknown')}")
            return None
        
        # Analyze the query
        config_params = analyze_query_for_config(query_to_analyze, metric.get('metric_name', 'Unknown'))
        
        # Create structured configuration
        structured_config = create_query_config(
            date_field=config_params['date_field'],
            query_field_name=config_params['date_field'],
            trunc_type=config_params['trunc_type'],
            aggregation_type=config_params['aggregation_type'],
            aggregation_field=config_params['aggregation_field'],
            is_fiscal_year=config_params['is_fiscal_year'],
            supports_districts=config_params['supports_districts'],
            is_ytd_query=config_params['is_ytd_query'],
            distinct=config_params.get('distinct', False),
            description=f"Auto-generated config for {metric.get('metric_name', 'Unknown')}"
        )
        
        return structured_config
        
    except Exception as e:
        logger.error(f"Error creating structured config for metric {metric.get('metric_name', 'Unknown')}: {e}")
        return None

def migrate_metrics_to_structured_queries(dry_run: bool = True) -> Dict[str, Any]:
    """
    Migrate all metrics to use structured query configuration.
    
    Args:
        dry_run: If True, only analyze and report changes without updating the database
        
    Returns:
        Dictionary containing migration results
    """
    results = {
        'total_metrics': 0,
        'successful_migrations': 0,
        'failed_migrations': 0,
        'skipped_metrics': 0,
        'errors': []
    }
    
    try:
        # Get database connection
        conn = get_connection()
        cursor = conn.cursor()
        
        # Get all active metrics
        cursor.execute("""
            SELECT id, metric_name, metric_key, ytd_query, metric_query, metadata
            FROM metrics 
            WHERE is_active = true
            ORDER BY id
        """)
        
        metrics = cursor.fetchall()
        results['total_metrics'] = len(metrics)
        
        logger.info(f"Found {len(metrics)} active metrics to migrate")
        
        for metric_row in metrics:
            metric_id, metric_name, metric_key, ytd_query, metric_query, metadata = metric_row
            
            try:
                # Check if metric already has structured config
                if metadata and metadata.get('query_config'):
                    logger.info(f"Metric {metric_name} already has structured config, skipping")
                    results['skipped_metrics'] += 1
                    continue
                
                # Create metric info dictionary
                metric_info = {
                    'id': metric_id,
                    'metric_name': metric_name,
                    'metric_key': metric_key,
                    'ytd_query': ytd_query,
                    'metric_query': metric_query,
                    'metadata': metadata or {}
                }
                
                # Create structured configuration
                structured_config = create_structured_config_for_metric(metric_info)
                
                if not structured_config:
                    logger.warning(f"Failed to create structured config for metric {metric_name}")
                    results['failed_migrations'] += 1
                    continue
                
                if dry_run:
                    logger.info(f"DRY RUN: Would update metric {metric_name} with structured config")
                    results['successful_migrations'] += 1
                else:
                    # Update the metric with structured configuration
                    cursor.execute("""
                        UPDATE metrics 
                        SET metadata = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (json.dumps(structured_config), metric_id))
                    
                    logger.info(f"Updated metric {metric_name} with structured config")
                    results['successful_migrations'] += 1
                
            except Exception as e:
                error_msg = f"Error processing metric {metric_name}: {e}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
                results['failed_migrations'] += 1
        
        if not dry_run:
            conn.commit()
            logger.info("Migration completed successfully")
        else:
            logger.info("Dry run completed - no changes made to database")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        error_msg = f"Database error during migration: {e}"
        logger.error(error_msg)
        results['errors'].append(error_msg)
    
    return results

def main():
    """Main function to run the migration."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate metrics to structured query configuration')
    parser.add_argument('--dry-run', action='store_true', default=True,
                        help='Run in dry-run mode (default: True)')
    parser.add_argument('--execute', action='store_true',
                        help='Actually execute the migration (overrides --dry-run)')
    
    args = parser.parse_args()
    
    # If --execute is specified, override dry_run
    dry_run = not args.execute
    
    logger.info(f"Starting migration {'(DRY RUN)' if dry_run else '(EXECUTING)'}")
    
    results = migrate_metrics_to_structured_queries(dry_run=dry_run)
    
    # Print results
    print("\n" + "="*50)
    print("MIGRATION RESULTS")
    print("="*50)
    print(f"Total metrics: {results['total_metrics']}")
    print(f"Successful migrations: {results['successful_migrations']}")
    print(f"Failed migrations: {results['failed_migrations']}")
    print(f"Skipped metrics: {results['skipped_metrics']}")
    
    if results['errors']:
        print(f"\nErrors ({len(results['errors'])}):")
        for error in results['errors']:
            print(f"  - {error}")
    
    if dry_run:
        print("\nThis was a dry run. Use --execute to actually update the database.")
    else:
        print("\nMigration completed successfully!")

if __name__ == "__main__":
    main()
