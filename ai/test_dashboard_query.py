import os
import json
from datetime import datetime, timedelta
from tools.data_fetcher import set_dataset
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_dashboard_queries():
    """Load the dashboard queries from the JSON file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    queries_file = os.path.join(script_dir, 'data', 'dashboard_queries.json')
    
    with open(queries_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def test_query(query_name, query_data):
    """Test a specific query and save its results."""
    logger.info(f"Testing query: {query_name}")
    
    # Initialize context variables
    context_variables = {}
    
    # Get query details
    endpoint = query_data['endpoint']
    monthly_query = query_data['queries']['Monthly']
    
    # Set start date to 10 years ago
    start_date = (datetime.now() - timedelta(days=365*10)).strftime('%Y-%m-%d')
    query_modified = monthly_query.replace('start_date', f"'{start_date}'")
    
    # Try to fetch the data
    result = set_dataset(
        context_variables=context_variables,
        endpoint=endpoint,
        query=query_modified
    )
    
    if 'error' in result:
        logger.error(f"Error setting dataset: {result['error']}")
        return None
        
    if 'dataset' not in context_variables:
        logger.error("No dataset found in context variables")
        return None
        
    dataset = context_variables['dataset']
    logger.info(f"Successfully fetched {len(dataset)} records")
    
    # Save the results
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'data', 'dashboard_data')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, f"{query_name}_data.json")
    
    # Convert DataFrame to JSON format
    data_json = dataset.to_json(orient='records')
    data_dict = {
        'metadata': query_data,
        'data': json.loads(data_json),
        'generated_at': datetime.now().isoformat(),
        'query_url': result.get('queryURL', '')
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data_dict, f, indent=2)
    
    logger.info(f"Saved results to {output_file}")
    return output_file

def main():
    """Main function to test all dashboard queries."""
    queries = load_dashboard_queries()
    
    for query_name, query_data in queries.items():
        try:
            output_file = test_query(query_name, query_data)
            if output_file:
                logger.info(f"Successfully processed {query_name}")
            else:
                logger.error(f"Failed to process {query_name}")
        except Exception as e:
            logger.error(f"Error processing {query_name}: {str(e)}", exc_info=True)

if __name__ == '__main__':
    main() 