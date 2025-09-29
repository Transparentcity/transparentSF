"""
Simple Parcel Lookup Service

A lightweight service that fetches parcel data on-demand and caches it efficiently.
Uses existing lat/lon coordinates from business data for direct spatial matching.

Key Features:
- Fetches parcel data only when needed
- Caches data in memory for the duration of the request
- Uses existing coordinates for efficient spatial joins
- No startup overhead
"""

import logging
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

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
    prepared_geometry: Optional[Any] = None

class SimpleParcelLookup:
    """Simple on-demand parcel lookup service"""
    
    def __init__(self):
        self.parcels: Dict[str, ParcelInfo] = {}
        self.parcels_by_zoning: Dict[str, List[str]] = {}
        self.spatial_index = []
        self.cache_loaded = False
        self.cache_timestamp = None
    
    def _ensure_cache_loaded(self):
        """Load parcel data if not already cached"""
        if self.cache_loaded and self.cache_timestamp:
            # Cache is valid for 1 hour
            if time.time() - self.cache_timestamp < 3600:
                return True
        
        logger.info("Loading parcel data for spatial lookup...")
        try:
            # Fetch parcel data
            result = fetch_data_from_api({
                'endpoint': 'fizh-zaxt',
                'query': 'SELECT mapblklot, zoning_sim, heightlimi, shape_area, shape_leng, the_geom'
            })
            
            if not result or 'data' not in result:
                logger.error("Failed to fetch parcel data")
                return False
            
            parcels_data = result['data']
            logger.info(f"Fetched {len(parcels_data)} parcels")
            
            # Process parcels
            self.parcels = {}
            self.parcels_by_zoning = {}
            self.spatial_index = []
            
            for parcel_data in parcels_data:
                try:
                    mapblklot = parcel_data.get('mapblklot', '')
                    if not mapblklot:
                        continue
                    
                    # Parse geometry
                    geometry = self._parse_geometry(parcel_data.get('the_geom'))
                    
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
                    
                    # Add to spatial index
                    if geometry:
                        self.spatial_index.append((mapblklot, parcel_info))
                
                except Exception as e:
                    logger.warning(f"Error processing parcel {parcel_data.get('mapblklot', 'unknown')}: {e}")
                    continue
            
            self.cache_loaded = True
            self.cache_timestamp = time.time()
            logger.info(f"Successfully loaded {len(self.parcels)} parcels with {len(self.parcels_by_zoning)} zoning categories")
            return True
            
        except Exception as e:
            logger.error(f"Error loading parcel data: {e}")
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
    
    def lookup_parcel_by_coordinates(self, lat: float, lon: float, buffer_meters: int = 50) -> Optional[ParcelInfo]:
        """Look up parcel by coordinates"""
        if not self._ensure_cache_loaded():
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
    
    def get_zoning_categories(self) -> List[str]:
        """Get all available zoning categories"""
        if not self._ensure_cache_loaded():
            return []
        return list(self.parcels_by_zoning.keys())
    
    def get_commercial_zoning_categories(self) -> List[str]:
        """Get only commercial zoning categories"""
        commercial_prefixes = ['C-', 'NC-', 'NCD', 'CCB', 'CRNC', 'CVR', 'MUO']
        all_zones = self.get_zoning_categories()
        commercial_zones = []
        
        for zone in all_zones:
            for prefix in commercial_prefixes:
                if zone.startswith(prefix) or zone == prefix:
                    commercial_zones.append(zone)
                    break
        
        return sorted(commercial_zones)
    
    def get_parcels_by_zoning(self, zoning: str) -> List[ParcelInfo]:
        """Get all parcels with a specific zoning designation"""
        if not self._ensure_cache_loaded():
            return []
        mapblklots = self.parcels_by_zoning.get(zoning, [])
        return [self.parcels[mapblklot] for mapblklot in mapblklots if mapblklot in self.parcels]
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            'parcels_count': len(self.parcels),
            'zoning_categories': len(self.parcels_by_zoning),
            'spatial_index_size': len(self.spatial_index),
            'cache_loaded': self.cache_loaded,
            'cache_timestamp': self.cache_timestamp
        }

# Global instance
parcel_lookup = SimpleParcelLookup()

def get_parcel_lookup() -> SimpleParcelLookup:
    """Get the global parcel lookup instance"""
    return parcel_lookup
