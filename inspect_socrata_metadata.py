import requests
import json

# Example Socrata dataset (SF Police Incidents) or similar
# Using a known SF dataset ID if possible, or searching for one.
# SF Police Incidents: wg3w-h783
DATASET_ID = "wg3w-h783"
DOMAIN = "data.sfgov.org"

url = f"https://{DOMAIN}/api/views/{DATASET_ID}.json"

try:
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    
    print("Metadata keys:", data.keys())
    
    if 'metadata' in data:
        print("\nMetadata section keys:", data['metadata'].keys())
        if 'custom_fields' in data['metadata']:
             print("\nCustom fields:", json.dumps(data['metadata']['custom_fields'], indent=2))
        
    # Check for other common frequency fields
    print("\nPublishing frequency:", data.get('publishingFrequency'))
    
except Exception as e:
    print(f"Error: {e}")




