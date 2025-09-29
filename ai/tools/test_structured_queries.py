"""
Test script for structured query configuration.

This script tests the structured query approach with metric #84 to ensure
it works correctly and replaces the regex-based parsing.
"""

import os
import sys
import json
import logging
from typing import Dict, Any

# Add the parent directory to the path so we can import from ai.tools
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_utils import execute_with_connection
from structured_query_parser import (
    get_structured_query_info,
    validate_query_config,
    extract_date_field_structured,
    extract_aggregation_function_structured,
    determine_date_field_name_structured,
    is_fiscal_year_query_structured,
    supports_districts_structured,
    is_ytd_query_structured
)
from query_config_schema import create_query_config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_metric_by_id(metric_id: int) -> Dict[str, Any]:
    """Get metric information by ID from the database."""
    def get_metric_operation(connection):
        cursor = connection.cursor()
        
        cursor.execute("""
            SELECT 
                id, metric_name, metric_key, category, subcategory, endpoint,
                summary, definition, data_sf_url, ytd_query, metric_query,
                dataset_title, dataset_category, show_on_dash, item_noun,
                greendirection, location_fields, category_fields, metadata
            FROM metrics
            WHERE id = %s AND is_active = true
        """, (metric_id,))
        
        metric_row = cursor.fetchone()
        cursor.close()
        
        if not metric_row:
            return None
        
        return {
            'metric_id': str(metric_row[0]),
            'query_name': metric_row[1],
            'metric_key': metric_row[2],
            'top_category': metric_row[3],
            'subcategory': metric_row[4],
            'endpoint': metric_row[5],
            'summary': metric_row[6],
            'definition': metric_row[7],
            'data_sf_url': metric_row[8],
            'category_fields': metric_row[16] or [],
            'location_fields': metric_row[15] or [],
            'metadata': metric_row[18] or {},
            'query_data': {
                'ytd_query': metric_row[9],
                'metric_query': metric_row[10],
                'id': metric_row[0],
                'endpoint': metric_row[5]
            }
        }
    
    try:
        result = execute_with_connection(get_metric_operation)
        if result['status'] == 'success':
            return result['result']
        else:
            logger.error(f"Error getting metric {metric_id}: {result.get('message', 'Unknown error')}")
            return None
    except Exception as e:
        logger.error(f"Error getting metric {metric_id}: {e}")
        return None

def test_metric_84():
    """Test the structured query approach with metric #84."""
    logger.info("Testing structured query approach with metric #84")
    
    # Get metric #84
    metric_info = get_metric_by_id(84)
    if not metric_info:
        logger.error("Metric #84 not found")
        return False
    
    logger.info(f"Found metric: {metric_info['query_name']}")
    logger.info(f"Endpoint: {metric_info['endpoint']}")
    logger.info(f"Category fields: {metric_info['category_fields']}")
    logger.info(f"Location fields: {metric_info['location_fields']}")
    logger.info(f"Metadata: {json.dumps(metric_info['metadata'], indent=2)}")
    
    # Test structured query parsing
    try:
        query_info = get_structured_query_info(metric_info)
        logger.info(f"Structured query info: {json.dumps(query_info, indent=2)}")
        
        # Validate configuration
        is_valid, errors = validate_query_config(metric_info)
        if is_valid:
            logger.info("✅ Query configuration is valid")
        else:
            logger.warning(f"❌ Query configuration is invalid: {errors}")
            
        # Test individual extraction functions
        date_field = extract_date_field_structured(metric_info)
        logger.info(f"Date field: {date_field}")
        
        aggregation = extract_aggregation_function_structured(metric_info)
        logger.info(f"Aggregation function: {aggregation}")
        
        date_field_name = determine_date_field_name_structured(metric_info, 'month')
        logger.info(f"Date field name: {date_field_name}")
        
        is_fiscal = is_fiscal_year_query_structured(metric_info)
        logger.info(f"Is fiscal year: {is_fiscal}")
        
        supports_dist = supports_districts_structured(metric_info)
        logger.info(f"Supports districts: {supports_dist}")
        
        is_ytd = is_ytd_query_structured(metric_info)
        logger.info(f"Is YTD query: {is_ytd}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error testing structured query parsing: {e}")
        return False

def create_test_config_for_metric_84():
    """Create a test configuration for metric #84."""
    logger.info("Creating test configuration for metric #84")
    
    # Get the metric
    metric_info = get_metric_by_id(84)
    if not metric_info:
        logger.error("Metric #84 not found")
        return None
    
    # Analyze the query to create configuration
    ytd_query = metric_info['query_data'].get('ytd_query', '')
    if not ytd_query:
        logger.error("No YTD query found for metric #84")
        return None
    
    logger.info(f"Analyzing YTD query: {ytd_query}")
    
    # Create structured configuration based on query analysis
    # Analyze the actual query to extract the correct parameters
    if "AVG(date_diff_d(completed_date, filed_date))" in ytd_query:
        # This is a building permit completion time metric
        config = create_query_config(
            date_field="completed_date",  # The date field used in date_trunc
            trunc_type="ymd",
            aggregation_type="AVG",
            aggregation_field="date_diff_d(completed_date, filed_date)",
            supports_districts=True,
            is_ytd_query=True,
            description="Building permit completion time metric - AVG of days between filed and completed dates"
        )
    else:
        # Fallback for other query types
        config = create_query_config(
            date_field="report_datetime",
            trunc_type="ymd",
            aggregation_type="COUNT",
            supports_districts=True,
            is_ytd_query=True,
            description="Test configuration for metric #84"
        )
    
    logger.info(f"Created test configuration: {json.dumps(config, indent=2)}")
    return config

def update_metric_84_with_config():
    """Update metric #84 with structured configuration."""
    logger.info("Updating metric #84 with structured configuration")
    
    # Create test configuration
    config = create_test_config_for_metric_84()
    if not config:
        return False
    
    def update_metric_operation(connection):
        cursor = connection.cursor()
        
        # Update the metric with the new configuration
        cursor.execute("""
            UPDATE metrics 
            SET metadata = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = 84
        """, (json.dumps(config),))
        
        cursor.close()
        return True
    
    try:
        result = execute_with_connection(update_metric_operation)
        if result['status'] == 'success':
            logger.info("✅ Successfully updated metric #84 with structured configuration")
            return True
        else:
            logger.error(f"Error updating metric #84: {result.get('message', 'Unknown error')}")
            return False
        
    except Exception as e:
        logger.error(f"Error updating metric #84: {e}")
        return False

def main():
    """Main test function."""
    print("="*60)
    print("TESTING STRUCTURED QUERY CONFIGURATION")
    print("="*60)
    
    # Test 1: Get metric #84
    print("\n1. Getting metric #84...")
    metric_info = get_metric_by_id(84)
    if not metric_info:
        print("❌ Failed to get metric #84")
        return
    
    print(f"✅ Found metric: {metric_info['query_name']}")
    
    # Test 2: Test current configuration
    print("\n2. Testing current configuration...")
    if test_metric_84():
        print("✅ Structured query parsing works")
    else:
        print("❌ Structured query parsing failed")
    
    # Test 3: Create and apply test configuration
    print("\n3. Creating and applying test configuration...")
    if update_metric_84_with_config():
        print("✅ Successfully updated metric #84")
        
        # Test 4: Test with new configuration
        print("\n4. Testing with new configuration...")
        updated_metric = get_metric_by_id(84)
        if updated_metric:
            query_info = get_structured_query_info(updated_metric)
            print(f"✅ New configuration works: {json.dumps(query_info, indent=2)}")
        else:
            print("❌ Failed to get updated metric")
    else:
        print("❌ Failed to update metric #84")
    
    print("\n" + "="*60)
    print("TEST COMPLETED")
    print("="*60)

def reset_metric_84_category_fields():
    """Reset metric 84 category_fields to only have supervisor_district."""
    logger.info("Resetting metric 84 category_fields")
    
    def update_metric_operation(connection):
        cursor = connection.cursor()
        
        # Reset category_fields to only have supervisor_district
        correct_category_fields = [
            {
                'name': 'supervisor_district',
                'fieldName': 'supervisor_district', 
                'description': 'Supervisor district where the permit was issued'
            }
        ]
        
        cursor.execute("""
            UPDATE metrics 
            SET category_fields = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = 84
        """, (json.dumps(correct_category_fields),))
        
        logger.info(f"Updated {cursor.rowcount} rows")
        cursor.close()
        return True
    
    try:
        result = execute_with_connection(update_metric_operation)
        if result['status'] == 'success':
            logger.info("✅ Successfully reset metric 84 category_fields")
            return True
        else:
            logger.error(f"Error resetting metric 84: {result.get('message', 'Unknown error')}")
            return False
        
    except Exception as e:
        logger.error(f"Error resetting metric 84: {e}")
        return False

def update_metric_84_where_conditions():
    """Update metric 84 structured config to include WHERE conditions."""
    logger.info("Updating metric 84 WHERE conditions")
    
    def update_metric_operation(connection):
        cursor = connection.cursor()
        
        # Get current metadata
        cursor.execute('SELECT metadata FROM metrics WHERE id = 84')
        result = cursor.fetchone()
        if not result or not result[0]:
            logger.error('No metadata found')
            return False
            
        metadata = result[0]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        
        # Add the WHERE conditions to the structured config
        if 'query_config' in metadata and 'ytd_config' in metadata['query_config']:
            metadata['query_config']['ytd_config']['custom_where_conditions'] = [
                "proposed_units > '0'",
                'filed_date IS NOT NULL', 
                'completed_date IS NOT NULL'
            ]
            
            # Update the database
            cursor.execute("""
                UPDATE metrics 
                SET metadata = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = 84
            """, (json.dumps(metadata),))
            
            logger.info(f"Updated {cursor.rowcount} rows with WHERE conditions")
            cursor.close()
            return True
        else:
            logger.error('Invalid metadata structure')
            return False
    
    try:
        result = execute_with_connection(update_metric_operation)
        if result['status'] == 'success':
            logger.info("✅ Successfully updated WHERE conditions")
            return True
        else:
            logger.error(f"Error updating WHERE conditions: {result.get('message', 'Unknown error')}")
            return False
        
    except Exception as e:
        logger.error(f"Error updating WHERE conditions: {e}")
        return False

if __name__ == "__main__":
    print("="*60)
    print("UPDATING METRIC 84 CONFIGURATION")
    print("="*60)
    
    # Reset category fields first
    if reset_metric_84_category_fields():
        print("✅ Category fields reset successfully")
    else:
        print("❌ Failed to reset category fields")
    
    # Update WHERE conditions
    if update_metric_84_where_conditions():
        print("✅ WHERE conditions updated successfully")
    else:
        print("❌ Failed to update WHERE conditions")
        
    print("\nNow running main test...")
    
    # Get metric #84 to verify
    metric_info = get_metric_by_id(84)
    if metric_info:
        print(f"✅ Found metric: {metric_info['query_name']}")
        print(f"WHERE conditions: {metric_info['metadata'].get('query_config', {}).get('ytd_config', {}).get('custom_where_conditions', [])}")
    else:
        print("❌ Failed to get metric #84")
        
    # Skip the main() test that overwrites the configuration
