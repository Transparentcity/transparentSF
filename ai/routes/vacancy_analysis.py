"""
Vacancy Analysis Route

This route provides a custom map interface for analyzing business vacancy rates
based on the business registration dataset. It shows open/closed status of businesses
and calculates vacancy rates with filtering by license type and business corridors.
"""

import logging
import os
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import psycopg2
import psycopg2.extras
from typing import Dict, Any, Optional, List
import json

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# Templates will be set by main app
templates = None

def set_templates(templates_instance):
    """Set templates instance from main app"""
    global templates
    templates = templates_instance

@router.get("/vacancy-analysis")
async def vacancy_analysis_page(request: Request):
    """Serve the vacancy analysis page"""
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    
    # Get Mapbox token from environment
    mapbox_token = os.getenv('MAPBOX_ACCESS_TOKEN')
    if not mapbox_token:
        logger.error("MAPBOX_ACCESS_TOKEN not found in environment")
        raise HTTPException(status_code=500, detail="Mapbox token not configured")
    
    return templates.TemplateResponse("vacancy_analysis.html", {
        "request": request,
        "mapbox_token": mapbox_token
    })

@router.get("/api/vacancy-analysis/data")
async def get_vacancy_data(
    industry_type: Optional[List[str]] = Query(None),  # NAICS code descriptions (multiple allowed)
    license_type: Optional[List[str]] = Query(None),   # License code descriptions (multiple allowed)
    business_corridor: Optional[str] = None,
    neighborhood: Optional[str] = None,  # Neighborhood analysis boundary
    corridor_only: bool = False,
    licensed_only: bool = False,
    street_level_only: bool = False,  # Filter to only street-level addresses
    district: Optional[str] = None,
    status_filter: Optional[str] = None  # 'open', 'closed', or None for all
):
    """Get vacancy analysis data with filters"""
    try:
        # Build the SOQL query for DataSF API
        # This query determines the status of each business location
        # by comparing the most recent open vs close dates
        base_query = """
        SELECT 
            location,
            dba_name,
            naic_code_description,
            lic_code_description,
            business_corridor,
            supervisor_district,
            dba_start_date,
            location_start_date,
            dba_end_date,
            location_end_date,
            administratively_closed,
            full_business_address
        WHERE location IS NOT NULL
        """
        
        # Add filters
        if industry_type and len(industry_type) > 0:
            # Create OR conditions for multiple industry types
            industry_conditions = []
            for itype in industry_type:
                industry_conditions.append(f"naic_code_description LIKE '%{itype}%'")
            base_query += f" AND ({' OR '.join(industry_conditions)})"
        
        if license_type and len(license_type) > 0:
            # Create OR conditions for multiple license types
            license_conditions = []
            for ltype in license_type:
                license_conditions.append(f"lic_code_description LIKE '%{ltype}%'")
            base_query += f" AND ({' OR '.join(license_conditions)})"
        
        if business_corridor:
            base_query += f" AND business_corridor LIKE '%{business_corridor}%'"
        
        if neighborhood:
            base_query += f" AND neighborhoods_analysis_boundaries LIKE '%{neighborhood}%'"
        
        if corridor_only:
            base_query += " AND business_corridor IS NOT NULL AND business_corridor != ''"
        
        if licensed_only:
            base_query += " AND lic_code_description IS NOT NULL AND lic_code_description != ''"
        
        if district:
            base_query += f" AND supervisor_district = '{district}'"
        
        # Add limit - increased for heatmap aggregation
        base_query += " LIMIT 50000"
        
        # Fetch data from DataSF API
        from ai.tools.data_fetcher import fetch_data_from_api
        
        query_object = {
            'endpoint': 'g8m3-pdis',
            'query': base_query
        }
        
        result = fetch_data_from_api(query_object)
        
        if not result or 'data' not in result:
            error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
            raise HTTPException(status_code=500, detail=f"Failed to fetch data from DataSF: {error_msg}")
        
        raw_data = result['data']
        
        # Group data by address and process vacancy status per address
        from collections import defaultdict
        from datetime import datetime
        
        def parse_date(date_str):
            if not date_str:
                return None
            try:
                return datetime.strptime(date_str.split('T')[0], '%Y-%m-%d')
            except:
                return None
        
        def get_building_location_key(address, coordinates):
            """Get a key for grouping by building location (street number + street + coordinates)"""
            if not address:
                return f"coords_{coordinates[0]:.6f}_{coordinates[1]:.6f}"
            
            # Normalize the address
            normalized = address.lower().strip()
            
            # Common street type abbreviations
            street_replacements = {
                ' street': ' st',
                ' avenue': ' ave',
                ' boulevard': ' blvd',
                ' drive': ' dr',
                ' road': ' rd',
                ' lane': ' ln',
                ' place': ' pl',
                ' court': ' ct',
                ' circle': ' cir',
                ' way': ' way',
                ' square': ' sq',
                ' terrace': ' ter',
                ' parkway': ' pkwy'
            }
            
            # Apply replacements
            for full_form, abbrev in street_replacements.items():
                normalized = normalized.replace(full_form, abbrev)
            
            # Remove extra spaces and standardize
            normalized = ' '.join(normalized.split())
            
            # Remove common suffixes that might cause duplicates
            suffixes_to_remove = [', san francisco, ca', ', sf, ca', ', california']
            for suffix in suffixes_to_remove:
                if normalized.endswith(suffix):
                    normalized = normalized[:-len(suffix)].strip()
            
            # Extract base address (remove unit/suite info for building grouping)
            # This removes things like "unit a", "suite 1", "#2", etc.
            import re
            # Remove unit/suite/apartment designations
            base_address = re.sub(r'\s+(unit|suite|apt|apartment|#|ste)\s*[a-z0-9]*$', '', normalized, flags=re.IGNORECASE)
            base_address = re.sub(r'\s+[a-z]$', '', base_address)  # Remove single letter at end (like "A")
            
            return base_address.strip()

        def classify_street_level(address):
            """Classify if an address is likely street-level/storefront or upper floor"""
            if not address:
                return True  # Default to street level if no address
            
            address_lower = address.lower().strip()
            
            # Debug logging for specific addresses
            if "1819 polk st" in address_lower or "212" in address_lower:
                logger.info(f"Classifying address: '{address}' (normalized: '{address_lower}')")
            
            # Explicit non-street-level indicators
            non_street_indicators = [
                'second floor', '2nd floor', '3rd floor', 'third floor', '4th floor', 'fourth floor',
                'fifth floor', '5th floor', 'sixth floor', '6th floor', 'seventh floor', '7th floor',
                'eighth floor', '8th floor', 'ninth floor', '9th floor', 'tenth floor', '10th floor',
                'floor 2', 'floor 3', 'floor 4', 'floor 5', 'floor 6', 'floor 7', 'floor 8', 'floor 9',
                'upstairs', 'upper level', 'mezzanine', 'penthouse'
            ]
            
            # Check for explicit floor indicators
            for indicator in non_street_indicators:
                if indicator in address_lower:
                    if "1819 polk st" in address_lower or "212" in address_lower:
                        logger.info(f"  Found floor indicator: '{indicator}' → Upper Floor")
                    return False
            
            # Extract potential unit/suite numbers using regex
            import re
            
            # Look for patterns like "Suite 201", "Unit 302", "Apt 205", "# 212", etc.
            # Only check for suite/unit numbers that are explicitly labeled, not street numbers
            unit_patterns = [
                r'\b(?:suite|ste|unit|apt|apartment)\s*(\d+)',  # Suite/Unit with space
                r'#\s*(\d+)',  # Hash symbol with optional space
                r'\b(?:room|rm)\s*(\d+)',  # Room numbers
            ]
            
            for pattern in unit_patterns:
                matches = re.findall(pattern, address_lower)
                for match in matches:
                    try:
                        number = int(match)
                        if "1819 polk st" in address_lower or "212" in address_lower:
                            logger.info(f"  Found unit number: {number} from pattern '{pattern}'")
                        
                        # If it's a 3+ digit number starting with 2 or higher, likely upper floor
                        if number >= 200:
                            if "1819 polk st" in address_lower or "212" in address_lower:
                                logger.info(f"  Unit {number} >= 200 → Upper Floor")
                            return False
                        # Numbers 100-199 could be ground floor suites
                        elif 100 <= number <= 199:
                            if "1819 polk st" in address_lower or "212" in address_lower:
                                logger.info(f"  Unit {number} in 100-199 range → Street Level")
                            return True  # Assume ground level
                    except ValueError:
                        continue
            
            # Check for single letter suites (A, B, C, etc.) - likely ground level
            if re.search(r'\b[a-z]\b$', address_lower) or re.search(r'\b(?:suite|ste|unit)\s*[a-z]\b', address_lower):
                if "1819 polk st" in address_lower or "212" in address_lower:
                    logger.info(f"  Found single letter unit → Street Level")
                return True
            
            # Default to street level if no clear indicators
            if "1819 polk st" in address_lower or "212" in address_lower:
                logger.info(f"  No clear indicators found → Street Level (default)")
            return True

        def normalize_full_address(address):
            """Normalize full address including unit info for exact deduplication"""
            if not address:
                return None
                
            # Convert to lowercase for consistent comparison
            normalized = address.lower().strip()
            
            # Common street type abbreviations
            street_replacements = {
                ' street': ' st',
                ' avenue': ' ave',
                ' boulevard': ' blvd',
                ' drive': ' dr',
                ' road': ' rd',
                ' lane': ' ln',
                ' place': ' pl',
                ' court': ' ct',
                ' circle': ' cir',
                ' way': ' way',
                ' square': ' sq',
                ' terrace': ' ter',
                ' parkway': ' pkwy'
            }
            
            # Apply replacements
            for full_form, abbrev in street_replacements.items():
                normalized = normalized.replace(full_form, abbrev)
            
            # Remove extra spaces and standardize
            normalized = ' '.join(normalized.split())
            
            # Remove common suffixes that might cause duplicates
            suffixes_to_remove = [', san francisco, ca', ', sf, ca', ', california']
            for suffix in suffixes_to_remove:
                if normalized.endswith(suffix):
                    normalized = normalized[:-len(suffix)].strip()
            
            return normalized

        def parse_location_coordinates(location_data):
            """Extract coordinates from location field"""
            coordinates = [0, 0]  # Default coordinates
            
            if location_data:
                if isinstance(location_data, dict):
                    # If it's already a dict with coordinates
                    if 'coordinates' in location_data:
                        coordinates = location_data['coordinates']
                    elif 'longitude' in location_data and 'latitude' in location_data:
                        coordinates = [location_data['longitude'], location_data['latitude']]
                elif isinstance(location_data, str) and location_data.startswith('POINT'):
                    try:
                        # Extract coordinates from "POINT (-122.435385968 37.637676996)"
                        coords_str = location_data.replace('POINT (', '').replace(')', '')
                        lon, lat = coords_str.split()
                        coordinates = [float(lon), float(lat)]
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse coordinates from '{location_data}': {e}")
                else:
                    logger.warning(f"Unexpected location data format: {type(location_data)} - {location_data}")
            
            return coordinates
        
        # Group businesses by building location, then by specific address within building
        building_groups = defaultdict(lambda: defaultdict(list))
        building_to_display_address = {}
        address_to_original = {}  # Map normalized address back to original for display
        
        logger.info(f"Processing {len(raw_data)} raw records")
        
        for record in raw_data:
            original_address = record.get('full_business_address')
            coordinates = parse_location_coordinates(record.get('location'))
            business_name = record.get('dba_name', 'Unknown')
            
            if original_address:
                # Get building location key (removes unit info)
                building_key = get_building_location_key(original_address, coordinates)
                # Get full address key (includes unit info)
                full_address_key = normalize_full_address(original_address)
                
                # Debug logging for 2139/2141 Polk Street
                if ("2139" in original_address or "2141" in original_address) and "polk" in original_address.lower():
                    logger.info(f"Polk Street business: {business_name}")
                    logger.info(f"  Original address: {original_address}")
                    logger.info(f"  Coordinates: {coordinates}")
                    logger.info(f"  Building key: {building_key}")
                    logger.info(f"  Full address key: {full_address_key}")
                
                # Store the best display address for this building (prefer shorter/more generic addresses)
                if building_key not in building_to_display_address:
                    building_to_display_address[building_key] = original_address
                else:
                    # If we already have an address, prefer the shorter one (less specific)
                    current_address = building_to_display_address[building_key]
                    if len(original_address) < len(current_address):
                        building_to_display_address[building_key] = original_address
                    elif len(original_address) == len(current_address):
                        # If same length, prefer the one without unit letters at the end
                        import re
                        if not re.search(r'\s+[a-z]$', original_address.lower()) and re.search(r'\s+[a-z]$', current_address.lower()):
                            building_to_display_address[building_key] = original_address
                
                # Store original address for this normalized version
                if full_address_key not in address_to_original:
                    address_to_original[full_address_key] = original_address
                
                # Group: building_groups[building_key][full_address_key] = [businesses]
                building_groups[building_key][full_address_key].append(record)
            else:
                # Fall back to coordinates for grouping
                building_key = f"coords_{coordinates[0]:.6f}_{coordinates[1]:.6f}"
                address_key = building_key  # Same as building key when no address
                
                building_to_display_address[building_key] = f"Coordinates: {coordinates[0]:.6f}, {coordinates[1]:.6f}"
                address_to_original[address_key] = f"Coordinates: {coordinates[0]:.6f}, {coordinates[1]:.6f}"
                
                building_groups[building_key][address_key].append(record)
        
        # Debug logging for building groups
        for building_key, addresses in building_groups.items():
            if "2139" in building_key and "polk" in building_key.lower():
                logger.info(f"Building {building_key} has {len(addresses)} addresses:")
                for addr_key, businesses in addresses.items():
                    logger.info(f"  Address {addr_key}: {len(businesses)} businesses")
                    for business in businesses:
                        logger.info(f"    - {business.get('dba_name', 'Unknown')}")
        
        processed_data = []
        
        # Sort buildings alphabetically for consistent display
        sorted_buildings = sorted(building_groups.keys())
        
        # Process each building group in sorted order
        for building_key in sorted_buildings:
            addresses_in_building = building_groups[building_key]
            building_display_address = building_to_display_address[building_key]
            
            # Collect all businesses in this building across all addresses
            all_building_businesses = []
            building_addresses_data = []
            
            # Sort addresses within the building
            sorted_addresses_in_building = sorted(addresses_in_building.keys())
            
            for address_key in sorted_addresses_in_building:
                address_businesses = addresses_in_building[address_key]
                address_display = address_to_original[address_key]
                
                # Process this specific address
                address_open_dates = []
                address_close_dates = []
                address_business_names = []
                
                for business in address_businesses:
                    address_business_names.append(business.get('dba_name', 'Unknown Business'))
                    
                    # Collect dates
                    dba_start = parse_date(business.get('dba_start_date'))
                    location_start = parse_date(business.get('location_start_date'))
                    dba_end = parse_date(business.get('dba_end_date'))
                    location_end = parse_date(business.get('location_end_date'))
                    
                    if dba_start:
                        address_open_dates.append(dba_start)
                    if location_start:
                        address_open_dates.append(location_start)
                    if dba_end:
                        address_close_dates.append(dba_end)
                    if location_end:
                        address_close_dates.append(location_end)
                
                # Determine address status - check if any business is currently active
                # An address is Open if ANY business there is currently open
                currently_open_businesses = []
                currently_closed_businesses = []
                
                for business in address_businesses:
                    # Calculate individual business status
                    dba_start = parse_date(business.get('dba_start_date'))
                    location_start = parse_date(business.get('location_start_date'))
                    dba_end = parse_date(business.get('dba_end_date'))
                    location_end = parse_date(business.get('location_end_date'))
                    
                    business_open_dates = [d for d in [dba_start, location_start] if d]
                    business_close_dates = [d for d in [dba_end, location_end] if d]
                    
                    business_most_recent_open = max(business_open_dates) if business_open_dates else None
                    business_most_recent_close = max(business_close_dates) if business_close_dates else None
                    
                    # Determine this business's current status
                    if not business_most_recent_close:
                        business_status = 'Open'
                    elif not business_most_recent_open:
                        business_status = 'Closed'
                    elif business_most_recent_close > business_most_recent_open:
                        business_status = 'Closed'
                    else:
                        business_status = 'Open'
                    
                    if business_status == 'Open':
                        currently_open_businesses.append(business)
                    else:
                        currently_closed_businesses.append(business)
                
                # Address is Open if ANY business is currently open
                address_status = 'Open' if currently_open_businesses else 'Closed'
                
                # Classify if this address is street-level
                is_street_level = classify_street_level(address_display)
                
                # Store address data
                building_addresses_data.append({
                    'address': address_display,
                    'status': address_status,
                    'is_street_level': is_street_level,
                    'business_count': len(address_businesses),
                    'businesses': address_businesses,
                    'business_names': address_business_names,
                    'currently_open_businesses': len(currently_open_businesses),
                    'currently_closed_businesses': len(currently_closed_businesses)
                })
                
                # Add to building total
                all_building_businesses.extend(address_businesses)
            
            # Use first address's coordinates for the building location
            first_business = all_building_businesses[0] if all_building_businesses else None
            if first_business:
                coordinates = parse_location_coordinates(first_business.get('location'))
            else:
                coordinates = [0, 0]
            
            # Calculate building-level statistics
            total_addresses = len(building_addresses_data)
            open_addresses = len([addr for addr in building_addresses_data if addr['status'] == 'Open'])
            closed_addresses = len([addr for addr in building_addresses_data if addr['status'] == 'Closed'])
            street_level_addresses = len([addr for addr in building_addresses_data if addr['is_street_level']])
            upper_floor_addresses = total_addresses - street_level_addresses
            total_businesses = len(all_building_businesses)
            
            # Overall building status (majority rule)
            building_status = 'Open' if open_addresses >= closed_addresses else 'Closed'
            
            # Create display name for building
            if total_addresses == 1:
                display_name = f"Building: {building_display_address}"
            else:
                display_name = f"Building: {building_display_address} ({total_addresses} addresses)"
            
            # Create the building record
            processed_record = {
                'location': {
                    'type': 'Point',
                    'coordinates': coordinates
                },
                'dba_name': display_name,
                'naic_code_description': first_business.get('naic_code_description') if first_business else None,
                'lic_code_description': first_business.get('lic_code_description') if first_business else None,
                'business_corridor': first_business.get('business_corridor') if first_business else None,
                'supervisor_district': first_business.get('supervisor_district') if first_business else None,
                'most_recent_open_date': None,  # Building level doesn't have single dates
                'most_recent_close_date': None,
                'status': building_status,
                'full_business_address': building_display_address,
                'building_key': building_key,
                'total_addresses': total_addresses,
                'open_addresses': open_addresses,
                'closed_addresses': closed_addresses,
                'street_level_addresses': street_level_addresses,
                'upper_floor_addresses': upper_floor_addresses,
                'total_businesses': total_businesses,
                'addresses_data': building_addresses_data,  # This contains the address → business hierarchy
                'business_count': total_addresses,  # For map display, show address count not business count
                'open_count': open_addresses,
                'closed_count': closed_addresses
            }
            
            processed_data.append(processed_record)
            
            # Debug logging for 2139 Polk Street
            if "2139" in building_display_address and "polk" in building_display_address.lower():
                logger.info(f"Created building record for {building_display_address}:")
                logger.info(f"  Total addresses: {total_addresses}")
                logger.info(f"  Total businesses: {total_businesses}")
                logger.info(f"  Addresses data: {len(building_addresses_data)} addresses")
                for addr_data in building_addresses_data:
                    logger.info(f"    {addr_data['address']}: {addr_data['business_count']} businesses")
        
        logger.info(f"Created {len(processed_data)} building records from {len(raw_data)} raw records")
        
        # Apply street-level filter if specified
        if street_level_only:
            # Filter buildings to only include those with street-level addresses
            filtered_data = []
            for building in processed_data:
                # Filter addresses_data to only street level
                street_level_addresses = [addr for addr in building['addresses_data'] if addr['is_street_level']]
                if street_level_addresses:
                    # Update building with only street-level addresses
                    building_copy = building.copy()
                    building_copy['addresses_data'] = street_level_addresses
                    building_copy['total_addresses'] = len(street_level_addresses)
                    building_copy['street_level_addresses'] = len(street_level_addresses)
                    building_copy['upper_floor_addresses'] = 0
                    building_copy['open_addresses'] = len([addr for addr in street_level_addresses if addr['status'] == 'Open'])
                    building_copy['closed_addresses'] = len([addr for addr in street_level_addresses if addr['status'] == 'Closed'])
                    building_copy['business_count'] = len(street_level_addresses)
                    building_copy['open_count'] = building_copy['open_addresses']
                    building_copy['closed_count'] = building_copy['closed_addresses']
                    filtered_data.append(building_copy)
            processed_data = filtered_data
            logger.info(f"Filtered to {len(processed_data)} buildings with street-level addresses")
        
        # Apply status filter if specified
        if status_filter:
            if status_filter.lower() == 'open':
                processed_data = [d for d in processed_data if d['status'] == 'Open']
                logger.info(f"Filtered to {len(processed_data)} open buildings")
            elif status_filter.lower() == 'closed':
                processed_data = [d for d in processed_data if d['status'] == 'Closed']
                logger.info(f"Filtered to {len(processed_data)} closed buildings")
        
        # Calculate comprehensive summary statistics
        total_buildings = len(processed_data)
        total_addresses = sum(len(building['addresses_data']) for building in processed_data)
        total_street_level = sum(len([addr for addr in building['addresses_data'] if addr['is_street_level']]) for building in processed_data)
        
        # Address-level statistics (all addresses)
        open_addresses = sum(len([addr for addr in building['addresses_data'] if addr['status'] == 'Open']) for building in processed_data)
        closed_addresses = sum(len([addr for addr in building['addresses_data'] if addr['status'] == 'Closed']) for building in processed_data)
        
        # Storefront-specific statistics (street-level only)
        open_storefronts = sum(len([addr for addr in building['addresses_data'] if addr['is_street_level'] and addr['status'] == 'Open']) for building in processed_data)
        closed_storefronts = sum(len([addr for addr in building['addresses_data'] if addr['is_street_level'] and addr['status'] == 'Closed']) for building in processed_data)
        
        # Calculate vacancy rates
        address_vacancy_rate = (closed_addresses / total_addresses * 100) if total_addresses > 0 else 0
        storefront_vacancy_rate = (closed_storefronts / total_street_level * 100) if total_street_level > 0 else 0
        
        filter_type = "street-level addresses" if street_level_only else "all addresses"
        logger.info(f"Summary stats ({filter_type}): {total_buildings} buildings, {total_addresses} addresses, {total_street_level} street-level, {open_addresses} open, {closed_addresses} closed, {address_vacancy_rate:.1f}% vacancy")
        
        # Group by district for district-level analysis - always count addresses
        district_stats = {}
        for record in processed_data:
            district = record['supervisor_district'] or 'Unknown'
            if district not in district_stats:
                district_stats[district] = {'total': 0, 'open': 0, 'closed': 0}
            
            # Always count addresses within each building
            for addr in record['addresses_data']:
                district_stats[district]['total'] += 1
                if addr['status'] == 'Open':
                    district_stats[district]['open'] += 1
                else:
                    district_stats[district]['closed'] += 1
        
        # Calculate vacancy rates by district
        for district in district_stats:
            stats = district_stats[district]
            stats['vacancy_rate'] = (stats['closed'] / stats['total'] * 100) if stats['total'] > 0 else 0
        
        return JSONResponse({
            "status": "success",
            "data": processed_data,
            "summary": {
                "total_buildings": total_buildings,
                "total_addresses": total_addresses,
                "total_street_level": total_street_level,
                "open_addresses": open_addresses,
                "closed_addresses": closed_addresses,
                "open_storefronts": open_storefronts,
                "closed_storefronts": closed_storefronts,
                "address_vacancy_rate": round(address_vacancy_rate, 2),
                "storefront_vacancy_rate": round(storefront_vacancy_rate, 2)
            },
            "district_stats": district_stats,
            "filters_applied": {
                "industry_type": industry_type,
                "license_type": license_type,
                "business_corridor": business_corridor,
                "corridor_only": corridor_only,
                "district": district
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_vacancy_data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting vacancy data: {str(e)}")

@router.get("/api/vacancy-analysis/filters")
async def get_filter_options():
    """Get available filter options for the vacancy analysis"""
    try:
        from ai.tools.data_fetcher import fetch_data_from_api
        
        # Get unique industry types (NAICS codes)
        industry_query = """
        SELECT DISTINCT naic_code_description 
        WHERE naic_code_description IS NOT NULL 
        AND naic_code_description != ''
        ORDER BY naic_code_description
        LIMIT 100
        """
        
        industry_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': industry_query
        })
        
        industry_types = []
        if industry_result and 'data' in industry_result:
            industry_types = [row['naic_code_description'] for row in industry_result['data']]
        
        # Get unique license types
        license_query = """
        SELECT DISTINCT lic_code_description 
        WHERE lic_code_description IS NOT NULL 
        AND lic_code_description != ''
        ORDER BY lic_code_description
        LIMIT 100
        """
        
        license_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': license_query
        })
        
        license_types = []
        if license_result and 'data' in license_result:
            license_types = [row['lic_code_description'] for row in license_result['data']]
        
        # Get unique business corridors
        corridor_query = """
        SELECT DISTINCT business_corridor 
        WHERE business_corridor IS NOT NULL 
        AND business_corridor != ''
        ORDER BY business_corridor
        LIMIT 100
        """
        
        corridor_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': corridor_query
        })
        
        business_corridors = []
        if corridor_result and 'data' in corridor_result:
            business_corridors = [row['business_corridor'] for row in corridor_result['data']]
        
        # Get districts
        district_query = """
        SELECT DISTINCT supervisor_district 
        WHERE supervisor_district IS NOT NULL 
        AND supervisor_district != ''
        ORDER BY supervisor_district
        LIMIT 20
        """
        
        district_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': district_query
        })
        
        districts = []
        if district_result and 'data' in district_result:
            districts = [str(row['supervisor_district']) for row in district_result['data']]
        
        # Get neighborhoods
        neighborhood_query = """
        SELECT DISTINCT neighborhoods_analysis_boundaries 
        WHERE neighborhoods_analysis_boundaries IS NOT NULL 
        AND neighborhoods_analysis_boundaries != ''
        ORDER BY neighborhoods_analysis_boundaries
        LIMIT 100
        """
        
        neighborhood_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': neighborhood_query
        })
        
        neighborhoods = []
        if neighborhood_result and 'data' in neighborhood_result:
            neighborhoods = [row['neighborhoods_analysis_boundaries'] for row in neighborhood_result['data']]
        
        # Ensure all arrays are properly initialized (never None/undefined)
        response_data = {
            "status": "success",
            "filters": {
                "industry_types": industry_types or [],
                "license_types": license_types or [],
                "business_corridors": business_corridors or [],
                "neighborhoods": neighborhoods or [],
                "districts": districts or []
            }
        }
        
        logger.info(f"Returning filter options: industry={len(response_data['filters']['industry_types'])}, "
                   f"license={len(response_data['filters']['license_types'])}, "
                   f"corridors={len(response_data['filters']['business_corridors'])}, "
                   f"neighborhoods={len(response_data['filters']['neighborhoods'])}, "
                   f"districts={len(response_data['filters']['districts'])}")
        
        return JSONResponse(response_data)
        
    except Exception as e:
        logger.error(f"Error in get_filter_options: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting filter options: {str(e)}")
