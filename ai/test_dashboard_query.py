import os
import json
from datetime import datetime, timedelta
from tools.data_fetcher import set_dataset
import logging
import pandas as pd
from generate_dashboard_metrics import get_date_ranges

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_error_details(result):
    """Extract detailed error information from the API response."""
    error_details = {
        'error': result.get('error'),
        'message': None,
        'error_code': None,
        'details': None,
        'data': None,
        'query': result.get('query'),
        'url': result.get('queryURL')
    }
    
    # Try to get more detailed error info from the response
    if 'response' in result:
        try:
            response = result['response']
            if isinstance(response, dict):
                error_details['message'] = response.get('message') or response.get('error')
                error_details['error_code'] = response.get('errorCode')
                error_details['details'] = response.get('details')
                error_details['data'] = response.get('data')
            elif isinstance(response, str):
                error_details['message'] = response
        except Exception as e:
            error_details['message'] = f"Error parsing response: {str(e)}"
    
    return error_details

def load_dashboard_queries():
    """Load the dashboard queries from the JSON file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    queries_file = os.path.join(script_dir, 'data', 'dashboard', 'dashboard_queries.json')
    
    with open(queries_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def test_business_query():
    """Test the business registrations query as a reference."""
    logger.info("Testing business registrations query")
    
    # Load queries
    queries = load_dashboard_queries()
    
    # Get business query details
    business_data = queries['economy']['business']
    endpoint = business_data['endpoint']
    query_data = business_data['queries']['🏢 New Business Registrations']
    
    # Get date ranges using the shared function
    date_ranges = get_date_ranges(endpoint=endpoint)
    
    # Initialize context variables for main queries
    context_variables = {}
    
    # Test YTD query
    ytd_query = query_data['ytd_query'].replace('last_year_start', f"'{date_ranges['last_year_start']}'").replace('current_date', f"'{date_ranges['this_year_end']}'")
    logger.info(f"Testing YTD query: {ytd_query}")
    
    ytd_result = set_dataset(
        context_variables=context_variables,
        endpoint=endpoint,
        query=ytd_query
    )
    
    if 'error' in ytd_result:
        error_details = extract_error_details(ytd_result)
        logger.error("Error in YTD query:")
        logger.error(f"  Error: {error_details['error']}")
        logger.error(f"  Message: {error_details['message']}")
        logger.error(f"  Error Code: {error_details['error_code']}")
        logger.error(f"  Details: {error_details['details']}")
        logger.error(f"  Data: {error_details['data']}")
        logger.error(f"  Query: {error_details['query']}")
        logger.error(f"  URL: {error_details['url']}")
    else:
        logger.info(f"YTD query successful, fetched {len(context_variables.get('dataset', []))} records")
        logger.info(f"YTD response: {ytd_result}")
    
    # Save the results
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'data', 'dashboard_data')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, "business_registrations_test.json")
    
    results = {
        'metadata': query_data,
        'ytd_result': {
            'query': ytd_query,
            'error': extract_error_details(ytd_result) if 'error' in ytd_result else None,
            'url': ytd_result.get('queryURL'),
            'data': context_variables.get('dataset').to_json(orient='records') if 'dataset' in context_variables else None,
            'raw_response': ytd_result
        },
        'generated_at': datetime.now().isoformat()
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"Saved test results to {output_file}")
    return not 'error' in ytd_result

def test_housing_query():
    """Test the housing completion query specifically."""
    logger.info("Testing housing completion query")
    
    # Load queries
    queries = load_dashboard_queries()
    
    # Get housing query details
    housing_data = queries['economy']['housing']
    endpoint = housing_data['endpoint']
    query_data = housing_data['queries']['🏠 New Housing Units Completed']
    
    # Get date ranges using the shared function
    date_ranges = get_date_ranges(endpoint=endpoint)
    
    # Initialize context variables for main queries
    context_variables = {}
    
    # Test YTD query
    ytd_query = query_data['ytd_query'].replace('last_year_start', f"'{date_ranges['last_year_start']}'").replace('current_date', f"'{date_ranges['this_year_end']}'")
    logger.info(f"Testing YTD query: {ytd_query}")
    
    ytd_result = set_dataset(
        context_variables=context_variables,
        endpoint=endpoint,
        query=ytd_query
    )
    
    if 'error' in ytd_result:
        error_details = extract_error_details(ytd_result)
        logger.error("Error in YTD query:")
        logger.error(f"  Error: {error_details['error']}")
        logger.error(f"  Message: {error_details['message']}")
        logger.error(f"  Error Code: {error_details['error_code']}")
        logger.error(f"  Details: {error_details['details']}")
        logger.error(f"  Data: {error_details['data']}")
        logger.error(f"  Query: {error_details['query']}")
        logger.error(f"  URL: {error_details['url']}")
    else:
        logger.info(f"YTD query successful, fetched {len(context_variables.get('dataset', []))} records")
        logger.info(f"YTD response: {ytd_result}")
    
    # Test metric query
    metric_query = query_data['metric_query'].replace('this_year_start', f"'{date_ranges['this_year_start']}'").replace('this_year_end', f"'{date_ranges['this_year_end']}'").replace('last_year_start', f"'{date_ranges['last_year_start']}'").replace('last_year_end', f"'{date_ranges['last_year_end']}'")
    logger.info(f"Testing metric query: {metric_query}")
    
    metric_result = set_dataset(
        context_variables=context_variables,
        endpoint=endpoint,
        query=metric_query
    )
    
    if 'error' in metric_result:
        error_details = extract_error_details(metric_result)
        logger.error("Error in metric query:")
        logger.error(f"  Error: {error_details['error']}")
        logger.error(f"  Message: {error_details['message']}")
        logger.error(f"  Error Code: {error_details['error_code']}")
        logger.error(f"  Details: {error_details['details']}")
        logger.error(f"  Data: {error_details['data']}")
        logger.error(f"  Query: {error_details['query']}")
        logger.error(f"  URL: {error_details['url']}")
    else:
        logger.info(f"Metric query successful, fetched {len(context_variables.get('dataset', []))} records")
        logger.info(f"Metric response: {metric_result}")
    
    # Save the results
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'data', 'dashboard_data')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, "housing_completions_test.json")
    
    results = {
        'metadata': query_data,
        'ytd_result': {
            'query': ytd_query,
            'error': extract_error_details(ytd_result) if 'error' in ytd_result else None,
            'url': ytd_result.get('queryURL'),
            'data': context_variables.get('dataset').to_json(orient='records') if 'dataset' in context_variables else None,
            'raw_response': ytd_result
        },
        'metric_result': {
            'query': metric_query,
            'error': extract_error_details(metric_result) if 'error' in metric_result else None,
            'url': metric_result.get('queryURL'),
            'data': context_variables.get('dataset').to_json(orient='records') if 'dataset' in context_variables else None,
            'raw_response': metric_result
        },
        'generated_at': datetime.now().isoformat()
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"Saved test results to {output_file}")
    return not ('error' in ytd_result or 'error' in metric_result)

if __name__ == '__main__':
    logger.info("Testing queries...")
    business_success = test_business_query()
    logger.info(f"Business query {'succeeded' if business_success else 'failed'}")
    
    housing_success = test_housing_query()
    logger.info(f"Housing query {'succeeded' if housing_success else 'failed'}") 