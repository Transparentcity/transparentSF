import json
import os
from pathlib import Path
from urllib.parse import urljoin

def extract_queries():
    # Path to the fixed datasets directory
    base_dir = Path(__file__).parent
    fixed_dir = base_dir / 'data' / 'datasets' / 'fixed'
    
    # Base URL for the API
    base_url = "https://data.sfgov.org/resource/"
    
    # List to store all queries
    queries = []
    
    # Process each JSON file in the fixed directory
    for json_file in fixed_dir.glob('*.json'):
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
                
                # Extract endpoint and create query object
                if 'endpoint' in data:
                    endpoint = data['endpoint']
                    # Construct API URL
                    api_url = urljoin(base_url, f"{endpoint}.json")
                    
                    # Create query object with only the fields we want
                    query_obj = {
                        'endpoint': endpoint,
                        'title': data.get('title', ''),
                        'description': data.get('description', ''),
                        'url': data.get('url', ''),
                        'category': data.get('category', ''),
                        'api_url': api_url,
                        'dataset_file': json_file.name
                    }
                    
                    # Add queries field only if it exists in the data
                    if 'queries' in data:
                        query_obj['queries'] = data['queries']
                    
                    queries.append(query_obj)
        except Exception as e:
            print(f"Error processing {json_file}: {str(e)}")
    
    # Save the extracted queries to queries.json in the ai directory
    output_path = base_dir / 'queries.json'
    with open(output_path, 'w') as f:
        json.dump({'queries': queries}, f, indent=4)
    
    print(f"Successfully extracted {len(queries)} queries to {output_path}")

if __name__ == '__main__':
    extract_queries() 