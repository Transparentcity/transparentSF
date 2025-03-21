#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

def load_json_file(file_path):
    """Load a JSON file and return its contents."""
    with open(file_path, 'r') as f:
        return json.load(f)

def save_json_file(data, file_path):
    """Save data to a JSON file with pretty formatting."""
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)

def get_dataset_info(endpoint_id, datasets_dir):
    """Get dataset information from the fixed datasets directory."""
    dataset_file = os.path.join(datasets_dir, f"{endpoint_id}.json")
    
    if not os.path.exists(dataset_file):
        print(f"Warning: Dataset file not found for endpoint {endpoint_id}")
        return None
    
    try:
        dataset_info = load_json_file(dataset_file)
        
        # Extract location and category fields
        location_fields = []
        category_fields = []
        
        for column in dataset_info.get("columns", []):
            field_name = column.get("fieldName", "").lower() if column.get("fieldName") else ""
            description = column.get("description", "").lower() if column.get("description") else ""
            
            # Identify location fields
            if any(term in field_name for term in ["location", "address", "lat", "lon", "longitude", "latitude", "geo", "point", "coordinates"]) or \
               any(term in description for term in ["location", "address", "coordinates", "geographic"]):
                location_fields.append({
                    "name": column.get("name", ""),
                    "fieldName": column.get("fieldName", ""),
                    "description": column.get("description", "")
                })
            
            # Identify category fields
            if any(term in field_name for term in ["category", "type", "class", "group", "status", "district", "neighborhood"]) or \
               "categor" in description or "classif" in description or "type of" in description:
                category_fields.append({
                    "name": column.get("name", ""),
                    "fieldName": column.get("fieldName", ""),
                    "description": column.get("description", "")
                })
        
        return {
            "title": dataset_info.get("title", ""),
            "category": dataset_info.get("category", ""),
            "location_fields": location_fields,
            "category_fields": category_fields
        }
    
    except Exception as e:
        print(f"Error loading dataset info for {endpoint_id}: {str(e)}")
        return None

def enhance_dashboard_queries(queries_file, datasets_dir, output_file):
    """Enhance dashboard queries with IDs and category fields."""
    # Load the dashboard queries
    dashboard_queries = load_json_file(queries_file)
    
    # Create a new enhanced structure
    enhanced_queries = {}
    
    # Initialize ID counter
    id_counter = 1
    
    # Create a mapping of metric names to IDs for reference
    id_mapping = {}
    
    # Process each category
    for category, subcategories in dashboard_queries.items():
        enhanced_queries[category] = {}
        
        # Process each subcategory
        for subcategory, subcategory_data in subcategories.items():
            enhanced_queries[category][subcategory] = {}
            
            # Copy existing subcategory data
            for key, value in subcategory_data.items():
                if key != "queries":
                    enhanced_queries[category][subcategory][key] = value
            
            # Get dataset info if endpoint is available
            endpoint_id = subcategory_data.get("endpoint")
            dataset_info = None
            if endpoint_id:
                dataset_info = get_dataset_info(endpoint_id, datasets_dir)
            
            # Add queries with enhancements
            enhanced_queries[category][subcategory]["queries"] = {}
            
            # Process each query
            if "queries" in subcategory_data:
                for metric_name, metric_data in subcategory_data["queries"].items():
                    # Assign a numeric ID
                    metric_id = id_counter
                    id_counter += 1
                    
                    # Store in mapping for reference
                    id_mapping[metric_id] = {
                        "name": metric_name,
                        "category": category,
                        "subcategory": subcategory
                    }
                    
                    # Create enhanced metric data
                    enhanced_metric = {
                        "id": metric_id,
                        **metric_data  # Copy all existing metric data
                    }
                    
                    # Add dataset info if available
                    if dataset_info:
                        enhanced_metric["dataset_title"] = dataset_info.get("title", "")
                        enhanced_metric["dataset_category"] = dataset_info.get("category", "")
                        enhanced_metric["location_fields"] = dataset_info.get("location_fields", [])
                        enhanced_metric["category_fields"] = dataset_info.get("category_fields", [])
                    
                    # Add to enhanced queries
                    enhanced_queries[category][subcategory]["queries"][metric_name] = enhanced_metric
    
    # Save the enhanced queries to the output file
    save_json_file(enhanced_queries, output_file)
    print(f"Enhanced dashboard queries saved to {output_file}")
    
    # Save the ID mapping for reference
    mapping_file = os.path.join(os.path.dirname(output_file), "metric_id_mapping.json")
    save_json_file(id_mapping, mapping_file)
    print(f"Metric ID mapping saved to {mapping_file}")

def main():
    # Define file paths
    script_dir = Path(__file__).parent
    queries_file = script_dir / "data" / "dashboard" / "dashboard_queries.json"
    datasets_dir = script_dir / "data" / "datasets"
    output_file = script_dir / "data" / "dashboard" / "dashboard_queries_enhanced.json"
    
    # Enhance dashboard queries
    enhance_dashboard_queries(queries_file, datasets_dir, output_file)

if __name__ == "__main__":
    main() 