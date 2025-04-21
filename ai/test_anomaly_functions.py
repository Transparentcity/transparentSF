import os
import sys
import json
import logging
from datetime import datetime, date

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add the parent directory to the path so we can import from webChat
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Import the functions to test
from webChat import query_anomalies_db, get_anomaly_details

# Custom JSON encoder to handle date objects
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

def print_json_results(result, test_name):
    """Helper function to print results in a consistent way"""
    print(f"\n----- {test_name} -----")
    print(f"Query returned {len(result.get('results', []))} results")
    print(json.dumps(result, indent=2, cls=CustomJSONEncoder))
    print("-" * 30)

def main():
    # Create a simple context_variables dict
    context_variables = {"dataset": None, "notes": ""}
    
    # Test 1: Basic recent query (baseline)
    logger.info("Test 1: Basic recent query")
    result = query_anomalies_db(context_variables, query_type='recent', limit=3)
    print_json_results(result, "BASELINE QUERY")
    
    # Store the first anomaly ID if we have results to test get_anomaly_details later
    first_anomaly_id = None
    if result.get('results') and len(result.get('results')) > 0:
        first_anomaly_id = result['results'][0].get('id')
        
        # Get the metric_id for use in later tests
        first_metric_id = result['results'][0].get('object_id')
        logger.info(f"Using metric_id {first_metric_id} for further tests")
    
    # Test 2: Query with specific period_type
    logger.info("Test 2: Query with specific period_type")
    result = query_anomalies_db(
        context_variables, 
        query_type='recent', 
        period_type='month',  # Filter to monthly data
        limit=3
    )
    print_json_results(result, "PERIOD_TYPE QUERY (month)")
    
    # Test 3: Query with specific metric_id
    if first_metric_id:
        logger.info(f"Test 3: Query with specific metric_id {first_metric_id}")
        result = query_anomalies_db(
            context_variables, 
            query_type='by_metric_id',
            metric_id=first_metric_id,
            limit=3
        )
        print_json_results(result, f"METRIC_ID QUERY ({first_metric_id})")
    
    # Test 4: Query with district filter
    logger.info("Test 4: Query with district filter")
    result = query_anomalies_db(
        context_variables, 
        query_type='recent',
        district_filter=0,  # Citywide
        limit=3
    )
    print_json_results(result, "DISTRICT QUERY (Citywide/0)")
    
    # Test 5: Query with specific group_filter 
    # First get a group value to filter on
    group_value = None
    if result.get('results') and len(result.get('results')) > 0:
        group_value = result['results'][0].get('group_value')
        
    if group_value:
        logger.info(f"Test 5: Query with specific group_filter '{group_value}'")
        result = query_anomalies_db(
            context_variables, 
            query_type='recent',
            group_filter=group_value,
            limit=3
        )
        print_json_results(result, f"GROUP_FILTER QUERY ('{group_value}')")
    
    # Test 6: Complex query with multiple filters
    if first_metric_id:
        logger.info("Test 6: Complex query with multiple filters")
        result = query_anomalies_db(
            context_variables, 
            query_type='by_metric_id',
            metric_id=first_metric_id,
            district_filter=0,
            period_type='month',
            limit=3
        )
        print_json_results(result, "COMPLEX QUERY (metric_id + district + period_type)")
    
    # Test 7: Test get_anomaly_details if we have an anomaly ID
    if first_anomaly_id:
        logger.info(f"Test 7: Getting details for anomaly ID {first_anomaly_id}")
        detail_result = get_anomaly_details(context_variables, first_anomaly_id)
        print_json_results(detail_result, f"ANOMALY DETAILS (ID: {first_anomaly_id})")
    else:
        logger.error("No anomaly ID found, skipping get_anomaly_details test")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"Error running test script: {str(e)}") 