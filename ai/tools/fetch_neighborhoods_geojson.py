"""
Fetch Analysis Neighborhoods GeoJSON from San Francisco DataSF
============================================================

This module fetches the analysis neighborhoods GeoJSON data from the city's
open data portal and saves it locally for use in map visualizations.
"""

import os
import json
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def fetch_neighborhoods_geojson() -> Optional[Dict[str, Any]]:
    """
    Fetch analysis neighborhoods GeoJSON data from San Francisco DataSF.
    
    Returns:
        GeoJSON data as dictionary, or None if fetch fails
    """
    try:
        # DataSF endpoint for analysis neighborhoods
        url = "https://data.sfgov.org/resource/j2bu-swwd.geojson"
        
        logger.info(f"Fetching analysis neighborhoods GeoJSON from {url}")
        
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        geojson_data = response.json()
        
        logger.info(f"Successfully fetched GeoJSON with {len(geojson_data.get('features', []))} features")
        
        return geojson_data
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch neighborhoods GeoJSON: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GeoJSON response: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching neighborhoods GeoJSON: {e}")
        return None

def save_geojson_to_file(geojson_data: Dict[str, Any], file_path: str) -> bool:
    """
    Save GeoJSON data to a local file.
    
    Args:
        geojson_data: GeoJSON data to save
        file_path: Path where to save the file
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(geojson_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved GeoJSON data to {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save GeoJSON to {file_path}: {e}")
        return False

def ensure_neighborhoods_geojson_exists() -> bool:
    """
    Ensure that the analysis neighborhoods GeoJSON file exists locally.
    If it doesn't exist, fetch it from the city and save it.
    
    Returns:
        True if file exists or was successfully created, False otherwise
    """
    # Path to the local GeoJSON file
    static_dir = Path(__file__).parent.parent / "static" / "data"
    geojson_path = static_dir / "sf_analysis_neighborhoods.geojson"
    
    # Check if file already exists
    if geojson_path.exists():
        logger.info(f"Analysis neighborhoods GeoJSON already exists at {geojson_path}")
        return True
    
    # Fetch from city
    logger.info("Analysis neighborhoods GeoJSON not found locally, fetching from city...")
    geojson_data = fetch_neighborhoods_geojson()
    
    if not geojson_data:
        logger.error("Failed to fetch neighborhoods GeoJSON from city")
        return False
    
    # Save to local file
    success = save_geojson_to_file(geojson_data, str(geojson_path))
    
    if success:
        logger.info("Successfully created local analysis neighborhoods GeoJSON file")
    else:
        logger.error("Failed to save neighborhoods GeoJSON locally")
    
    return success

if __name__ == "__main__":
    # Test the function
    logging.basicConfig(level=logging.INFO)
    success = ensure_neighborhoods_geojson_exists()
    print(f"Neighborhoods GeoJSON setup: {'SUCCESS' if success else 'FAILED'}")
