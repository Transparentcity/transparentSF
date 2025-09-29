"""
Dataset Cache Service

This module provides a comprehensive caching service for San Francisco datasets,
enabling fast in-memory spatial lookups without repeated API calls.

Key Features:
- Downloads and caches both AHBP_eligible_parcels and business registration datasets
- Creates spatial indexes for fast parcel lookup by coordinates
- Provides efficient in-memory spatial matching
- Handles dataset updates and cache invalidation
- Memory-efficient storage with proper indexing
"""

import logging
import json
import time
import os
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.prepared import prep
import pickle
from pathlib import Path

from ai.tools.data_fetcher import fetch_data_from_api

logger = logging.getLogger(__name__)

@dataclass
class ParcelInfo:
    """Structured parcel information"""
    mapblklot: str
    zoning_sim: str
    heightlimi: str
    shape_area: float
    shape_leng: float
    geometry: Optional[Polygon] = None
    prepared_geometry: Optional[Any] = None  # Prepared geometry for fast spatial queries

@dataclass
class BusinessInfo:
    """Structured business information"""
    full_business_address: str
    location: Optional[Tuple[float, float]]  # (lat, lon)
    dba_name: str
    supervisor_district: Optional[str]
    business_corridor: Optional[str]
    naic_code_description: Optional[str]
    lic_code_description: Optional[str]
    dba_start_date: Optional[str]
    location_start_date: Optional[str]
    dba_end_date: Optional[str]
    location_end_date: Optional[str]
    administratively_closed: Optional[str]

class DatasetCache:
    """Main dataset cache service"""
    
    def __init__(self, cache_dir: str = "ai/data/cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache storage
        self.parcels: Dict[str, ParcelInfo] = {}  # mapblklot -> ParcelInfo
        self.parcels_by_zoning: Dict[str, List[str]] = {}  # zoning_sim -> [mapblklot, ...]
        self.businesses: List[BusinessInfo] = []
        
        # Spatial index for fast parcel lookup
        self.spatial_index = None  # Will be a spatial index structure
        
        # Cache metadata
        self.cache_metadata = {
            'parcels_loaded': False,
            'businesses_loaded': False,
            'last_updated': None,
            'parcels_count': 0,
            'businesses_count': 0
        }
        
        # Cache file paths
        self.parcels_cache_file = self.cache_dir / "parcels_cache.pkl"
        self.businesses_cache_file = self.cache_dir / "businesses_cache.pkl"
        self.metadata_cache_file = self.cache_dir / "cache_metadata.json"
    
    def load_cache_metadata(self) -> bool:
        """Load cache metadata to check if cache is valid"""
        try:
            if self.metadata_cache_file.exists():
                with open(self.metadata_cache_file, 'r') as f:
                    self.cache_metadata = json.load(f)
                return True
        except Exception as e:
            logger.warning(f"Failed to load cache metadata: {e}")
        return False
    
    def save_cache_metadata(self):
        """Save cache metadata"""
        try:
            with open(self.metadata_cache_file, 'w') as f:
                json.dump(self.cache_metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cache metadata: {e}")
    
    def is_cache_valid(self, max_age_hours: int = 24) -> bool:
        """Check if cache is valid and not too old"""
        if not self.cache_metadata.get('last_updated'):
            return False
        
        try:
            last_updated = time.time() - self.cache_metadata['last_updated']
            return last_updated < (max_age_hours * 3600)
        except:
            return False
    
    def load_parcels_from_cache(self) -> bool:
        """Load parcels from cache file"""
        try:
            if self.parcels_cache_file.exists():
                with open(self.parcels_cache_file, 'rb') as f:
                    cached_data = pickle.load(f)
                    self.parcels = cached_data.get('parcels', {})
                    self.parcels_by_zoning = cached_data.get('parcels_by_zoning', {})
                
                # Rebuild prepared geometries and spatial index
                for parcel_info in self.parcels.values():
                    if parcel_info.geometry:
                        parcel_info.prepared_geometry = prep(parcel_info.geometry)
                
                self._build_spatial_index()
                
                logger.info(f"Loaded {len(self.parcels)} parcels from cache")
                return True
        except Exception as e:
            logger.warning(f"Failed to load parcels from cache: {e}")
        return False
    
    def save_parcels_to_cache(self):
        """Save parcels to cache file"""
        try:
            # Create a copy without prepared geometries for pickling
            parcels_for_cache = {}
            for mapblklot, parcel_info in self.parcels.items():
                parcels_for_cache[mapblklot] = ParcelInfo(
                    mapblklot=parcel_info.mapblklot,
                    zoning_sim=parcel_info.zoning_sim,
                    heightlimi=parcel_info.heightlimi,
                    shape_area=parcel_info.shape_area,
                    shape_leng=parcel_info.shape_leng,
                    geometry=parcel_info.geometry,
                    prepared_geometry=None  # Don't pickle prepared geometries
                )
            
            cache_data = {
                'parcels': parcels_for_cache,
                'parcels_by_zoning': self.parcels_by_zoning,
                'spatial_index': None  # Don't pickle spatial index, rebuild on load
            }
            with open(self.parcels_cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
            logger.info(f"Saved {len(self.parcels)} parcels to cache")
        except Exception as e:
            logger.error(f"Failed to save parcels to cache: {e}")
    
    def load_businesses_from_cache(self) -> bool:
        """Load businesses from cache file"""
        try:
            if self.businesses_cache_file.exists():
                with open(self.businesses_cache_file, 'rb') as f:
                    self.businesses = pickle.load(f)
                logger.info(f"Loaded {len(self.businesses)} businesses from cache")
                return True
        except Exception as e:
            logger.warning(f"Failed to load businesses from cache: {e}")
        return False
    
    def save_businesses_to_cache(self):
        """Save businesses to cache file"""
        try:
            with open(self.businesses_cache_file, 'wb') as f:
                pickle.dump(self.businesses, f)
            logger.info(f"Saved {len(self.businesses)} businesses to cache")
        except Exception as e:
            logger.error(f"Failed to save businesses to cache: {e}")
    
    def download_parcels_data(self) -> bool:
        """Download parcels data from DataSF API"""
        logger.info("Downloading parcels data from DataSF...")
        
        try:
            # Download all parcels data
            result = fetch_data_from_api({
                'endpoint': 'fizh-zaxt',
                'query': 'SELECT mapblklot, zoning_sim, heightlimi, shape_area, shape_leng, the_geom'
            })
            
            if not result or 'data' not in result:
                logger.error("Failed to fetch parcels data from API")
                return False
            
            parcels_data = result['data']
            logger.info(f"Downloaded {len(parcels_data)} parcels from API")
            
            # Process parcels data
            self.parcels = {}
            self.parcels_by_zoning = {}
            
            for parcel_data in parcels_data:
                try:
                    mapblklot = parcel_data.get('mapblklot', '')
                    if not mapblklot:
                        continue
                    
                    # Parse geometry if available
                    geometry = None
                    if 'the_geom' in parcel_data and parcel_data['the_geom']:
                        geometry = self._parse_geometry(parcel_data['the_geom'])
                    
                    # Create parcel info
                    parcel_info = ParcelInfo(
                        mapblklot=mapblklot,
                        zoning_sim=parcel_data.get('zoning_sim', ''),
                        heightlimi=parcel_data.get('heightlimi', ''),
                        shape_area=float(parcel_data.get('shape_area', 0)),
                        shape_leng=float(parcel_data.get('shape_leng', 0)),
                        geometry=geometry
                    )
                    
                    # Add prepared geometry for fast spatial queries
                    if geometry:
                        parcel_info.prepared_geometry = prep(geometry)
                    
                    self.parcels[mapblklot] = parcel_info
                    
                    # Index by zoning
                    zoning = parcel_info.zoning_sim
                    if zoning:
                        if zoning not in self.parcels_by_zoning:
                            self.parcels_by_zoning[zoning] = []
                        self.parcels_by_zoning[zoning].append(mapblklot)
                
                except Exception as e:
                    logger.warning(f"Error processing parcel {parcel_data.get('mapblklot', 'unknown')}: {e}")
                    continue
            
            # Build spatial index
            self._build_spatial_index()
            
            # Update metadata
            self.cache_metadata['parcels_loaded'] = True
            self.cache_metadata['parcels_count'] = len(self.parcels)
            self.cache_metadata['last_updated'] = time.time()
            
            logger.info(f"Successfully processed {len(self.parcels)} parcels")
            return True
            
        except Exception as e:
            logger.error(f"Error downloading parcels data: {e}")
            return False
    
    def download_businesses_data(self) -> bool:
        """Download businesses data from DataSF API"""
        logger.info("Downloading businesses data from DataSF...")
        
        try:
            # Download all businesses data
            result = fetch_data_from_api({
                'endpoint': 'g8m3-pdis',
                'query': 'SELECT full_business_address, location, dba_name, supervisor_district, business_corridor, naic_code_description, lic_code_description, dba_start_date, location_start_date, dba_end_date, location_end_date, administratively_closed'
            })
            
            if not result or 'data' not in result:
                logger.error("Failed to fetch businesses data from API")
                return False
            
            businesses_data = result['data']
            logger.info(f"Downloaded {len(businesses_data)} businesses from API")
            
            # Process businesses data
            self.businesses = []
            
            for business_data in businesses_data:
                try:
                    # Parse coordinates
                    location = None
                    if 'location' in business_data and business_data['location']:
                        location = self._parse_coordinates(business_data['location'])
                    
                    # Create business info
                    business_info = BusinessInfo(
                        full_business_address=business_data.get('full_business_address', ''),
                        location=location,
                        dba_name=business_data.get('dba_name', ''),
                        supervisor_district=business_data.get('supervisor_district'),
                        business_corridor=business_data.get('business_corridor'),
                        naic_code_description=business_data.get('naic_code_description'),
                        lic_code_description=business_data.get('lic_code_description'),
                        dba_start_date=business_data.get('dba_start_date'),
                        location_start_date=business_data.get('location_start_date'),
                        dba_end_date=business_data.get('dba_end_date'),
                        location_end_date=business_data.get('location_end_date'),
                        administratively_closed=business_data.get('administratively_closed')
                    )
                    
                    self.businesses.append(business_info)
                
                except Exception as e:
                    logger.warning(f"Error processing business {business_data.get('dba_name', 'unknown')}: {e}")
                    continue
            
            # Update metadata
            self.cache_metadata['businesses_loaded'] = True
            self.cache_metadata['businesses_count'] = len(self.businesses)
            self.cache_metadata['last_updated'] = time.time()
            
            logger.info(f"Successfully processed {len(self.businesses)} businesses")
            return True
            
        except Exception as e:
            logger.error(f"Error downloading businesses data: {e}")
            return False
    
    def _parse_geometry(self, geom_data: Any) -> Optional[Polygon]:
        """Parse geometry data from DataSF format"""
        try:
            if isinstance(geom_data, dict) and 'coordinates' in geom_data:
                coords = geom_data['coordinates']
                if geom_data.get('type') == 'MultiPolygon':
                    # Convert MultiPolygon to Polygon (take first polygon)
                    if coords and len(coords) > 0 and len(coords[0]) > 0:
                        return Polygon(coords[0][0])
                elif geom_data.get('type') == 'Polygon':
                    if coords and len(coords) > 0:
                        return Polygon(coords[0])
            return None
        except Exception as e:
            logger.warning(f"Error parsing geometry: {e}")
            return None
    
    def _parse_coordinates(self, location_data: Any) -> Optional[Tuple[float, float]]:
        """Parse coordinates from location data"""
        try:
            if isinstance(location_data, dict) and 'coordinates' in location_data:
                coords = location_data['coordinates']
                if len(coords) >= 2:
                    return (float(coords[1]), float(coords[0]))  # (lat, lon)
            return None
        except Exception as e:
            logger.warning(f"Error parsing coordinates: {e}")
            return None
    
    def _build_spatial_index(self):
        """Build spatial index for fast parcel lookup"""
        logger.info("Building spatial index...")
        
        # For now, we'll use a simple list-based approach
        # In production, you might want to use R-tree or other spatial indexing
        self.spatial_index = []
        
        for mapblklot, parcel_info in self.parcels.items():
            if parcel_info.geometry:
                self.spatial_index.append((mapblklot, parcel_info))
        
        logger.info(f"Built spatial index with {len(self.spatial_index)} parcels")
    
    def initialize_cache(self, force_refresh: bool = False) -> bool:
        """Initialize the cache by loading from disk or downloading from API"""
        logger.info("Initializing dataset cache...")
        
        # Load metadata
        self.load_cache_metadata()
        
        # Check if we need to refresh
        if force_refresh or not self.is_cache_valid():
            logger.info("Cache is invalid or force refresh requested, downloading fresh data...")
            
            # Download fresh data
            parcels_success = self.download_parcels_data()
            businesses_success = self.download_businesses_data()
            
            if parcels_success:
                self.save_parcels_to_cache()
            if businesses_success:
                self.save_businesses_to_cache()
            
            self.save_cache_metadata()
            
            return parcels_success and businesses_success
        else:
            logger.info("Cache is valid, loading from disk...")
            
            # Load from cache
            parcels_loaded = self.load_parcels_from_cache()
            businesses_loaded = self.load_businesses_from_cache()
            
            return parcels_loaded and businesses_loaded
    
    def lookup_parcel_by_coordinates(self, lat: float, lon: float, buffer_meters: int = 50) -> Optional[ParcelInfo]:
        """Look up parcel by coordinates using spatial index"""
        if not self.spatial_index:
            return None
        
        try:
            point = Point(lon, lat)  # Shapely uses (x, y) = (lon, lat)
            
            # Find parcels that contain the point
            for mapblklot, parcel_info in self.spatial_index:
                if parcel_info.prepared_geometry and parcel_info.prepared_geometry.contains(point):
                    return parcel_info
            
            # If no exact match, find the closest parcel within buffer
            buffer_degrees = buffer_meters / 111000.0  # Rough conversion
            buffered_point = point.buffer(buffer_degrees)
            
            for mapblklot, parcel_info in self.spatial_index:
                if parcel_info.geometry and parcel_info.geometry.intersects(buffered_point):
                    return parcel_info
            
            return None
            
        except Exception as e:
            logger.error(f"Error in spatial lookup: {e}")
            return None
    
    def lookup_parcel_by_address(self, address: str) -> Optional[ParcelInfo]:
        """Look up parcel by address (requires geocoding)"""
        # This would require geocoding the address first
        # For now, return None - we'll implement this if needed
        logger.warning("Address-based parcel lookup not implemented yet")
        return None
    
    def get_zoning_categories(self) -> List[str]:
        """Get all available zoning categories"""
        return list(self.parcels_by_zoning.keys())
    
    def get_parcels_by_zoning(self, zoning: str) -> List[ParcelInfo]:
        """Get all parcels with a specific zoning designation"""
        mapblklots = self.parcels_by_zoning.get(zoning, [])
        return [self.parcels[mapblklot] for mapblklot in mapblklots if mapblklot in self.parcels]
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            'parcels_count': len(self.parcels),
            'businesses_count': len(self.businesses),
            'zoning_categories': len(self.parcels_by_zoning),
            'spatial_index_size': len(self.spatial_index) if self.spatial_index else 0,
            'cache_metadata': self.cache_metadata
        }

# Global cache instance
dataset_cache = DatasetCache()

def initialize_dataset_cache(force_refresh: bool = False) -> bool:
    """Initialize the global dataset cache"""
    return dataset_cache.initialize_cache(force_refresh)

def get_dataset_cache() -> DatasetCache:
    """Get the global dataset cache instance"""
    return dataset_cache
