"""
Vacancy Analysis Route

This route provides a custom map interface for analyzing business vacancy rates
based on the business registration dataset. It shows open/closed status of businesses
and calculates vacancy rates with filtering by license type and business corridors.
"""

import logging
import os
import csv
import io
from fastapi import APIRouter, Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, Optional, List
import json
from background_jobs import job_manager

# Configure logging
logger = logging.getLogger(__name__)

def parse_address_components(full_address):
    """
    Parse address into components: street_number, street_name, unit.
    
    Returns a tuple: (street_number, street_name, unit)
    - street_number: The numeric part at the start (e.g., "2139", "123-125")
    - street_name: The street name without unit (e.g., "POLK ST")
    - unit: The unit identifier if present (e.g., "A", "101", "2B"), empty string if none
    
    Examples:
        "2139 POLK ST A" -> ("2139", "POLK ST", "A")
        "123 MAIN ST #101" -> ("123", "MAIN ST", "101")
        "2139 A POLK ST" -> ("2139", "POLK ST", "A")
        "456 MARKET ST" -> ("456", "MARKET ST", "")
    """
    import re
    if not full_address:
        return ("", "", "")
    
    addr = full_address.strip().upper()
    
    # Extract street number at the start (may include hyphens like "123-125")
    number_match = re.match(r'^(\d+(?:-\d+)?)', addr)
    if not number_match:
        # No number found, return the whole thing as street name
        return ("", addr, "")
    
    street_number = number_match.group(1)
    rest = addr[len(street_number):].strip()
    
    # Try to extract unit identifier
    unit = ""
    unit_patterns = [
        (r'^([A-Z]|#[A-Z0-9]+)\s+(.+)$', 1, 2),  # Unit at beginning: "A POLK ST" or "#101 POLK ST"
        (r'\s+([A-Z]|#[A-Z0-9]+)$', 1, None),   # Unit at end: "POLK ST A" or "POLK ST #101"
        (r'\s+(?:APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM|FL|FLOOR)\s*([A-Z0-9]+)', 1, None),  # "POLK ST APT 101"
        (r'\s+(\d+[A-Z]?)$', 1, None),  # Trailing number: "POLK ST 101" or "POLK ST 2A"
    ]
    
    for pattern, unit_group, street_group in unit_patterns:
        match = re.search(pattern, rest) if street_group is None else re.match(pattern, rest)
        if match:
            unit = match.group(unit_group).replace('#', '').strip()
            if street_group:
                rest = match.group(street_group)
            else:
                # Remove the matched unit from rest
                rest = rest[:match.start()].strip()
            break
    
    # Clean up street name
    street_name = rest.strip()
    street_name = re.sub(r'\s+', ' ', street_name)
    
    return (street_number, street_name, unit)


def extract_building_address(full_address):
    """Extract building address without unit letters/numbers"""
    if not full_address:
        return None

    import re

    # Convert to uppercase for consistent processing
    address = full_address.upper()

    # Remove common unit patterns more comprehensively
    # Apply patterns in order of specificity (most specific first)

    # Pattern 1: Handle unit letters that appear after street number (like "2139 A POLK ST")
    # This handles cases where unit letter(s) are between number and street name
    # Remove one or more consecutive unit letters: "2139 A B C POLK ST" → "2139 POLK ST"
    address = re.sub(r'(\d+)\s+([A-Z](?:\s+[A-Z])*\s+)([A-Z]+)', r'\1 \3', address)
    
    # Pattern 1a: Handle single unit letter after street number (like "2139 B POLK ST")
    # This is a more specific pattern for single letters
    address = re.sub(r'(\d+)\s+([A-Z])\s+([A-Z]+)', r'\1 \3', address)

    # Pattern 1b: Handle # unit letters in the middle (like "2139 POLK ST #C")
    address = re.sub(r'(\d+\s+[A-Z\s]+)\s+#[A-Z]\s*$', r'\1', address)
    # Pattern 1c: Handle # unit numbers in the middle/end with optional space (like "123 MAIN ST # 301")
    address = re.sub(r'(\d+\s+[A-Z\s]+)\s+#\s*\d+\s*$', r'\1', address)

    # Pattern 2: Handle complex unit combinations at the end (multiple letters, separators)
    # Handle patterns like: A B C, A, B, C, A & B, A B AND C, etc.
    # This needs to handle multiple consecutive unit letters at the end
    address = re.sub(r'\s+[A-Z](?:\s+[A-Z])*\s*$', '', address)  # A B C, A B, A patterns at end

    # Pattern 3: Remove numbered units with letters at the end (1A, 2B, etc.)
    address = re.sub(r'\s+\d+[A-Z]?\s*$', '', address)

    # Pattern 4: Remove simple unit letters at the end (A, B, C, etc.)
    address = re.sub(r'\s+[A-Z]\s*$', '', address)

    # Pattern 5: Remove numbered units (#1, # 2, etc.) and unit letters with # (#A, #B, #C)
    address = re.sub(r'\s+#\s*\d+\s*$', '', address)
    address = re.sub(r'\s+#[A-Z]\s*$', '', address)  # Remove #A, #B, #C at end

    # Pattern 6: Remove apartment/suite/unit indicators with numbers/letters
    address = re.sub(r'\s+(?:APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM|FL|FLOOR)\s*[A-Z0-9]+\s*$', '', address, flags=re.IGNORECASE)

    # Pattern 6: Clean up any remaining artifacts and normalize whitespace
    address = re.sub(r'\s+', ' ', address)  # Normalize whitespace
    address = re.sub(r'\s*[,;]+\s*$', '', address)  # Remove trailing commas/semicolons
    address = re.sub(r'\s*&\s*$', '', address)  # Remove trailing &
    address = re.sub(r'\s+AND\s*$', '', address)  # Remove trailing AND

    # Pattern 7: Handle repeated unit letters (like "2139 B POLK ST B")
    address = re.sub(r'\b([A-Z])\s+([A-Z\s]+)\s+\1\b', r'\1 \2', address)  # Remove duplicate unit letters

    return address.strip()

# Create router
router = APIRouter()

# Templates will be set by main app
templates = None

def set_templates(templates_instance):
    """Set templates instance from main app"""
    global templates
    templates = templates_instance

@router.get("/vacancy")
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

@router.get("/api/vacancy/data")
async def get_vacancy_data(
    industry_type: Optional[List[str]] = Query(None),  # NAICS code descriptions (multiple allowed)
    license_type: Optional[List[str]] = Query(None),   # License code descriptions (multiple allowed)
    business_corridor: Optional[List[str]] = Query(None),
    corridor_only: bool = False,
    district: Optional[List[str]] = Query(None),
    limit: Optional[int] = Query(None),
    commercial_tax_filing_only: bool = False,  # Whether to filter to only commercial tax filing locations
    business_search: Optional[str] = Query(None)  # Business name search
):
    """Get vacancy analysis data with filters using real-time API"""
    try:
        # Debug logging for filters
        logger.info(f"Received filters - industry_type: {industry_type}, license_type: {license_type}, business_search: {business_search}")
        print(f"DEBUG: industry_type={industry_type}, license_type={license_type}, business_search={business_search}")
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
            full_business_address,
            certificate_number
        WHERE location IS NOT NULL
        """
        
        # Add filters - check if parameters have meaningful values
        if industry_type is not None and str(industry_type) != "annotation=Union[List[str], NoneType] required=False default=None alias='industry_type' json_schema_extra={}":
            if isinstance(industry_type, list) and industry_type:
                industry_conditions = " OR ".join([f"naic_code_description LIKE '%{str(item)}%'" for item in industry_type])
                base_query += f" AND ({industry_conditions})"
            elif not isinstance(industry_type, list) and str(industry_type).strip():
                industry_conditions = f"naic_code_description LIKE '%{str(industry_type)}%'"
                base_query += f" AND ({industry_conditions})"
        
        if license_type is not None and str(license_type) != "annotation=Union[List[str], NoneType] required=False default=None alias='license_type' json_schema_extra={}":
            if isinstance(license_type, list) and license_type:
                license_conditions = " OR ".join([f"lic_code_description LIKE '%{str(item)}%'" for item in license_type])
                base_query += f" AND ({license_conditions})"
            elif not isinstance(license_type, list) and str(license_type).strip():
                license_conditions = f"lic_code_description LIKE '%{str(license_type)}%'"
                base_query += f" AND ({license_conditions})"
        
        if business_corridor is not None and str(business_corridor) != "annotation=Union[List[str], NoneType] required=False default=None alias='business_corridor' json_schema_extra={}":
            if isinstance(business_corridor, list) and business_corridor:
                corridor_conditions = " OR ".join([f"business_corridor LIKE '%{str(item)}%'" for item in business_corridor])
                base_query += f" AND ({corridor_conditions})"
            elif not isinstance(business_corridor, list) and str(business_corridor).strip():
                corridor_conditions = f"business_corridor LIKE '%{str(business_corridor)}%'"
                base_query += f" AND ({corridor_conditions})"
        
        if corridor_only:
            base_query += " AND business_corridor IS NOT NULL AND business_corridor != ''"
        
        if district is not None and str(district) != "annotation=Union[List[str], NoneType] required=False default=None alias='district' json_schema_extra={}":
            if isinstance(district, list) and district:
                # Filter out empty strings and whitespace-only values
                valid_districts = [str(d).strip() for d in district if str(d).strip()]
                if valid_districts:
                    district_conditions = " OR ".join([f"supervisor_district = '{item}'" for item in valid_districts])
                    base_query += f" AND ({district_conditions})"
            elif not isinstance(district, list) and str(district).strip():
                district_conditions = f"supervisor_district = '{str(district).strip()}'"
                base_query += f" AND ({district_conditions})"
        
        # Add business search filter
        if business_search is not None and str(business_search).strip():
            search_term = str(business_search).strip()
            # Use LIKE for case-insensitive search on business name (DataSF uses LIKE which is case-insensitive)
            business_search_condition = f"dba_name LIKE '%{search_term}%'"
            base_query += f" AND ({business_search_condition})"
            logger.info(f"Added business search filter: {business_search_condition}")
        
        # Add limit - use smaller limit for business search to improve performance
        if limit:
            base_query += f" LIMIT {limit}"
        elif business_search is not None and str(business_search).strip():
            # For business search, use a much smaller limit since we're filtering by name
            base_query += " LIMIT 1000"  # Much smaller limit for search results
            logger.info("Using reduced limit (1000) for business search to improve performance")
        else:
            base_query += " LIMIT 350000"  # Increased to handle all ~337k business records
        
        logger.info(f"Executing SOQL query: {base_query}")
        print(f"DEBUG: Final SOQL query: {base_query}")
        
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
        
        # Always fetch commercial tax filing addresses for filtering
        commercial_tax_filing_addresses = set()
        logger.info("Fetching commercial tax filing data for address matching...")
        try:
            # Fetch ALL commercial tax filing data (not just ones with BAN)
            tax_query = """
            SELECT 
                ban,
                parcelnumber,
                parcelsitusaddress,
                taxyear,
                filed,
                vacant,
                rate,
                filertype,
                entity,
                lin,
                linaddress,
                longitude,
                latitude,
                analysis_neighborhood,
                supervisor_district
            """
            
            tax_result = fetch_data_from_api({
                'endpoint': 'rzkk-54yv',
                'query': tax_query
            })
            
            if tax_result and 'data' in tax_result:
                # Collect all addresses from commercial tax filing data
                for tax_record in tax_result['data']:
                    parcel_address = tax_record.get('parcelsitusaddress', '')
                    lin_address = tax_record.get('linaddress', '')
                    
                    if parcel_address:
                        commercial_tax_filing_addresses.add(parcel_address.upper().strip())
                    if lin_address:
                        commercial_tax_filing_addresses.add(lin_address.upper().strip())
                
                logger.info(f"Collected {len(commercial_tax_filing_addresses)} unique addresses from commercial tax filing data")
            else:
                logger.warning("Failed to fetch commercial tax filing data")
        except Exception as e:
            logger.error(f"Error fetching commercial tax filing data: {str(e)}")
        
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
        
        # Group businesses by building (extract base address without unit letters/numbers)
        address_groups = defaultdict(list)
        
        def extract_building_address(full_address):
            """Extract building address without unit letters/numbers"""
            if not full_address:
                return None

            import re

            # Convert to uppercase for consistent processing
            address = full_address.upper()

            # Remove common unit patterns more comprehensively
            # Apply patterns in order of specificity (most specific first)

            # Pattern 1: Handle unit letters that appear after street number (like "2139 A POLK ST")
            # This handles cases where unit letter(s) are between number and street name
            # Remove one or more consecutive unit letters: "2139 A B C POLK ST" → "2139 POLK ST"
            address = re.sub(r'(\d+)\s+([A-Z](?:\s+[A-Z])*\s+)([A-Z]+)', r'\1 \3', address)
            
            # Pattern 1a: Handle single unit letter after street number (like "2139 B POLK ST")
            # This is a more specific pattern for single letters
            address = re.sub(r'(\d+)\s+([A-Z])\s+([A-Z]+)', r'\1 \3', address)

            # Pattern 1b: Handle # unit letters in the middle (like "2139 POLK ST #C")
            address = re.sub(r'(\d+\s+[A-Z\s]+)\s+#[A-Z]\s*$', r'\1', address)
            # Pattern 1c: Handle # unit numbers in the middle/end with optional space (like "123 MAIN ST # 301")
            address = re.sub(r'(\d+\s+[A-Z\s]+)\s+#\s*\d+\s*$', r'\1', address)

            # Pattern 2: Handle complex unit combinations at the end (multiple letters, separators)
            # Handle patterns like: A B C, A, B, C, A & B, A B AND C, etc.
            # This needs to handle multiple consecutive unit letters at the end
            address = re.sub(r'\s+[A-Z](?:\s+[A-Z])*\s*$', '', address)  # A B C, A B, A patterns at end

            # Pattern 3: Remove numbered units with letters at the end (1A, 2B, etc.)
            address = re.sub(r'\s+\d+[A-Z]?\s*$', '', address)

            # Pattern 4: Remove simple unit letters at the end (A, B, C, etc.)
            address = re.sub(r'\s+[A-Z]\s*$', '', address)

            # Pattern 5: Remove numbered units (#1, # 2, etc.) and unit letters with # (#A, #B, #C)
            address = re.sub(r'\s+#\s*\d+\s*$', '', address)
            address = re.sub(r'\s+#[A-Z]\s*$', '', address)  # Remove #A, #B, #C at end

            # Pattern 6: Remove apartment/suite/unit indicators with numbers/letters
            address = re.sub(r'\s+(?:APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM|FL|FLOOR)\s*[A-Z0-9]+\s*$', '', address, flags=re.IGNORECASE)

            # Pattern 6: Clean up any remaining artifacts and normalize whitespace
            address = re.sub(r'\s+', ' ', address)  # Normalize whitespace
            address = re.sub(r'\s*[,;]+\s*$', '', address)  # Remove trailing commas/semicolons
            address = re.sub(r'\s*&\s*$', '', address)  # Remove trailing &
            address = re.sub(r'\s+AND\s*$', '', address)  # Remove trailing AND

            # Pattern 7: Handle repeated unit letters (like "2139 B POLK ST B")
            address = re.sub(r'\b([A-Z])\s+([A-Z\s]+)\s+\1\b', r'\1 \2', address)  # Remove duplicate unit letters

            return address.strip()

        def create_distinct_address(full_address):
            """Create a canonicalized version of the address for display"""
            if not full_address:
                return None

            import re

            # Convert to uppercase for consistent processing
            address = full_address.upper()

            # Normalize street type abbreviations
            address = re.sub(r'\bSTREET\b', 'ST', address)
            address = re.sub(r'\bAVENUE\b', 'AVE', address)
            address = re.sub(r'\bBOULEVARD\b', 'BLVD', address)
            address = re.sub(r'\bROAD\b', 'RD', address)
            address = re.sub(r'\bLANE\b', 'LN', address)
            address = re.sub(r'\bDRIVE\b', 'DR', address)
            address = re.sub(r'\bWAY\b', 'WAY', address)
            address = re.sub(r'\bPLACE\b', 'PL', address)
            address = re.sub(r'\bCOURT\b', 'CT', address)
            address = re.sub(r'\bTERRACE\b', 'TER', address)

            # Handle unit letters - move them to standard position
            # Pattern 1: "2139 POLK ST C" -> "2139 C POLK ST" (unit after street type like ST/AVE/RD)
            address = re.sub(r'^(\d+)\s+([A-Z]+)\s+(ST|AVE|BLVD|RD|LN|DR|WAY|PL|CT|TER)\s+([A-Z])\s*$', r'\1 \4 \2 \3', address)

            # Pattern 2: "2139 POLK ST A" -> "2139 A POLK ST" (unit after street name)
            address = re.sub(r'^(\d+)\s+([A-Z]+)\s+([A-Z]+)\s+([A-Z])\s*$', r'\1 \4 \2 \3', address)

            # Pattern 3: "2139 POLK ST #C" -> "2139 C POLK ST" (unit with #)
            address = re.sub(r'^(\d+)\s+([A-Z\s]+)\s+#([A-Z])\s*$', r'\1 \3 \2', address)

            # Pattern 4: Handle repeated unit letters (like "2139 B POLK ST B")
            address = re.sub(r'\b([A-Z])\s+([A-Z\s]+)\s+\1\b', r'\1 \2', address)  # Remove duplicate unit letters

            # Remove extra whitespace
            address = re.sub(r'\s+', ' ', address)

            return address.strip()

        def is_upper_floor_address(full_address: str) -> bool:
            """
            Return True if the address indicates an upper-floor/unit (non-storefront).
            
            Updated logic: All units with numbers are considered upper floor.
            Letters (A, B, C, etc.) are considered ground floor.
            """
            if not full_address:
                return False
            import re
            s = full_address.upper()
            
            # Indicators of units/floors: #digits (allow optional space), APT/UNIT/STE/SUITE/RM/ROOM, FL/FLOOR with optional digits, ordinals
            if re.search(r'#\s*[0-9]+', s):
                return True
            if re.search(r'\b(APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM)\b\s*[A-Z0-9]*', s):
                return True
            if re.search(r'\b(FL|FLOOR)\b\s*[0-9A-Z]*', s):
                return True
            if re.search(r'\b(2ND|3RD|4TH|5TH|6TH|7TH|8TH|9TH|10TH|11TH|12TH)\b', s):
                return True
            
            # Parse address to get unit component
            street_number, street_name, unit = parse_address_components(full_address)
            
            # If unit exists and contains any digits, it's upper floor
            if unit:
                # Check if unit contains any numeric digits
                if re.search(r'\d', unit):
                    return True
                # Units with letter+number combo (e.g., "2A", "23W") are upper floor
                if re.search(r'[A-Z]\d|\d[A-Z]', unit):
                    return True
            
            # Trailing numeric token (e.g., "945 TARAVAL ST 1045")
            # All numeric units are now considered upper floor (changed from >= 200)
            trailing_number_match = re.search(r'\s([0-9]+)\s*$', s)
            if trailing_number_match:
                # Any trailing number is upper floor (not just >= 200)
                return True
            
            # Addresses ending with letter+number combo (e.g., "101 LOMBARD ST 23W", "101 LOMBARD ST 409W")
            if re.search(r'\s[0-9]+[A-Z]\s*$', s):
                return True
            
            # Addresses with unit/apartment numbers using hyphen or underscore
            # (e.g., "2443 FILLMORE ST 380-2710", "2443 FILLMORE ST 380_2266")
            # This pattern indicates unit/apartment numbers, not street-level addresses
            if re.search(r'\s\d+[-_]\d+', s):
                return True
            
            return False

        for record in raw_data:
            # Group by normalized building address (so "2139 A POLK ST" and "2139 POLK ST A" group together)
            full_address = record.get('full_business_address')
            building_address = extract_building_address(full_address)

            if building_address:
                address_key = building_address
            else:
                # Fall back to coordinates for grouping
                coordinates = parse_location_coordinates(record.get('location'))
                address_key = f"{coordinates[0]:.6f},{coordinates[1]:.6f}"

            address_groups[address_key].append(record)
        
        processed_data = []
        
        # Process each building group
        for address_key, businesses in address_groups.items():
            # Get coordinates from the first business (they should all be at the same location)
            primary_business = businesses[0] if businesses else None
            if not primary_business:
                continue

            coordinates = parse_location_coordinates(primary_business.get('location'))

            # Group businesses by their individual original addresses within this building
            individual_address_groups = defaultdict(list)
            original_addresses_by_canonical = defaultdict(set)
            for business in businesses:
                original_address = business.get('full_business_address', '')
                canonical_address = create_distinct_address(original_address) or original_address
                individual_address_groups[canonical_address].append(business)
                if original_address:
                    original_addresses_by_canonical[canonical_address].add(original_address)

            # Process each individual address within the building
            addresses_data = []
            streetfront_open_count = 0
            streetfront_closed_count = 0
            total_business_count = 0

            for individual_address, address_businesses in individual_address_groups.items():
                total_business_count += len(address_businesses)

                if not address_businesses:
                    continue

                # Determine if this is a street-level address using robust detection
                is_street_level = not is_upper_floor_address(individual_address)

                # Check ALL businesses at this address - if ANY is open, address is open
                # This handles cases where one business closes but another is still open
                address_has_open_business = False
                address_has_closed_business = False
                
                # Get the most recent business for display purposes
                most_recent_business = max(address_businesses,
                    key=lambda b: max(
                        parse_date(b.get('dba_start_date')) or datetime.min,
                        parse_date(b.get('location_start_date')) or datetime.min
                    ))
                
                # Check status of each business at this address
                # Use same logic as determine_status function
                for business in address_businesses:
                    dba_start = parse_date(business.get('dba_start_date'))
                    location_start = parse_date(business.get('location_start_date'))
                    dba_end = parse_date(business.get('dba_end_date'))
                    location_end = parse_date(business.get('location_end_date'))
                    admin_closed = business.get('administratively_closed', False)
                    
                    # Determine if this business is currently open (same logic as determine_status)
                    # Closed if administratively closed
                    if admin_closed:
                        business_status = 'Closed'
                    else:
                        # Normalize dates for comparison
                        def normalize_date(d):
                            if d is None:
                                return None
                            if hasattr(d, 'date'):
                                return d.date()
                            return d
                        
                        dba_start_date = normalize_date(dba_start)
                        dba_end_date = normalize_date(dba_end)
                        location_start_date = normalize_date(location_start)
                        location_end_date = normalize_date(location_end)
                        
                        # Closed if opened and closed on the same day (likely data error)
                        if dba_start_date and dba_end_date and dba_start_date == dba_end_date:
                            business_status = 'Closed'
                        elif location_start_date and location_end_date and location_start_date == location_end_date:
                            business_status = 'Closed'
                        else:
                            # Get the most recent start and end dates
                            all_start_dates = [d for d in [dba_start_date, location_start_date] if d is not None]
                            all_end_dates = [d for d in [dba_end_date, location_end_date] if d is not None]
                            
                            most_recent_start = max(all_start_dates) if all_start_dates else None
                            most_recent_end = max(all_end_dates) if all_end_dates else None
                            
                            # If there's an end date, check if it's more recent than the start date
                            if most_recent_end:
                                # If no start date, or end date is more recent than start date, it's closed
                                if most_recent_start is None or most_recent_end >= most_recent_start:
                                    business_status = 'Closed'
                                # If start date is more recent than end date, it reopened and is open
                                else:
                                    business_status = 'Open'
                            # If there's a start date and no end date, it's open
                            elif most_recent_start:
                                business_status = 'Open'
                            # No dates available - default to open (assume active if no closure info)
                            else:
                                business_status = 'Open'
                    
                    if business_status == 'Open':
                        address_has_open_business = True
                    else:
                        address_has_closed_business = True
                
                # Determine individual address status: open if ANY business is open
                if address_has_open_business:
                    individual_status = 'Open'
                elif address_has_closed_business:
                    individual_status = 'Closed'
                else:
                    individual_status = 'Unknown'
                
                # Get dates from most recent business for display
                dba_start = parse_date(most_recent_business.get('dba_start_date'))
                location_start = parse_date(most_recent_business.get('location_start_date'))
                dba_end = parse_date(most_recent_business.get('dba_end_date'))
                location_end = parse_date(most_recent_business.get('location_end_date'))

                # Count streetfront addresses
                if is_street_level:
                    if individual_status == 'Open':
                        streetfront_open_count += 1
                    else:
                        streetfront_closed_count += 1

                # Create distinct_address for canonical display (loop key is already canonical)
                distinct_address = individual_address
                # Choose a representative original address for compatibility (if available)
                representative_original = None
                if individual_address in original_addresses_by_canonical and original_addresses_by_canonical[individual_address]:
                    representative_original = next(iter(original_addresses_by_canonical[individual_address]))

                # Get the correct dates for this specific business
                business_open_date = dba_start
                business_close_date = dba_end

                # Add to addresses_data - simplified to show only current status
                addresses_data.append({
                    'address': representative_original or distinct_address,  # Prefer original for compatibility
                    'distinct_address': distinct_address,  # Canonicalized address for display
                    'status': individual_status,
                    'is_street_level': is_street_level,
                    'business_count': len(address_businesses),  # Historical count for reference
                    'dba_name': most_recent_business.get('dba_name', 'Unknown Business'),  # Current business name
                    'open_date': business_open_date.isoformat() if business_open_date else None,  # Current open date
                    'close_date': business_close_date.isoformat() if business_close_date else None,  # Current close date
                    'businesses': address_businesses  # Include ALL businesses for this address
                })

            # Determine overall building status based on streetfront addresses
            if streetfront_closed_count > 0 and streetfront_open_count == 0:
                overall_status = 'Closed'
            elif streetfront_open_count > 0:
                overall_status = 'Open'
            else:
                overall_status = 'Unknown'

            # Create display name - use primary business name
            display_name = primary_business.get('dba_name', 'Unknown Business')
            
            # Create a canonicalized distinct_address for the building
            building_distinct_address = create_distinct_address(address_key)

            # For buildings, we use the canonicalized distinct_address as the display address
            display_address = building_distinct_address if building_distinct_address else address_key

            # Compute upper-floor counts from addresses_data
            upper_floor_open_count = sum(1 for a in addresses_data if a.get('is_street_level') is False and a.get('status') == 'Open')
            upper_floor_closed_count = sum(1 for a in addresses_data if a.get('is_street_level') is False and a.get('status') == 'Closed')
            total_upper_floor_addresses = upper_floor_open_count + upper_floor_closed_count

            # Filter by commercial tax filing locations if requested
            if commercial_tax_filing_only and commercial_tax_filing_addresses:
                # Check if this building's address matches any commercial tax filing address
                building_addresses = [display_address.upper().strip()]
                if building_distinct_address:
                    building_addresses.append(building_distinct_address.upper().strip())
                if address_key:
                    building_addresses.append(address_key.upper().strip())
                
                # Check if any of the building addresses match commercial tax filing addresses
                has_match = False
                for addr in building_addresses:
                    if addr in commercial_tax_filing_addresses:
                        has_match = True
                        break
                
                # Skip this building if it doesn't match any commercial tax filing address
                if not has_match:
                    continue
            
            processed_record = {
                'location': {
                    'type': 'Point',
                    'coordinates': coordinates
                },
                'dba_name': display_name,
                'naic_code_description': primary_business.get('naic_code_description'),
                'lic_code_description': primary_business.get('lic_code_description'),
                'business_corridor': primary_business.get('business_corridor'),
                'supervisor_district': primary_business.get('supervisor_district'),
                'zoning_district': primary_business.get('zoning_district'),  # Add zoning district
                'status': overall_status,
                'full_business_address': display_address,  # Canonicalized building address for display
                'distinct_address': building_distinct_address,  # Canonicalized address
                'building_address': address_key,  # Normalized building address for reference
                'business_count': total_business_count,  # Total historical businesses across all addresses
                'streetfront_open_count': streetfront_open_count,
                'streetfront_closed_count': streetfront_closed_count,
                'total_streetfront_addresses': streetfront_open_count + streetfront_closed_count,
                'upper_floor_open_count': upper_floor_open_count,
                'upper_floor_closed_count': upper_floor_closed_count,
                'total_upper_floor_addresses': total_upper_floor_addresses,
                'addresses_data': addresses_data,  # Contains current status per canonicalized address
                'individual_addresses_count': len(addresses_data)
            }
            
            processed_data.append(processed_record)
        
        # Calculate summary statistics
        total_buildings = len(processed_data)

        # Count total streetfront addresses across all buildings
        total_streetfront_addresses = sum(d['total_streetfront_addresses'] for d in processed_data)
        open_streetfront_addresses = sum(d['streetfront_open_count'] for d in processed_data)
        closed_streetfront_addresses = sum(d['streetfront_closed_count'] for d in processed_data)
        streetfront_vacancy_rate = (closed_streetfront_addresses / total_streetfront_addresses * 100) if total_streetfront_addresses > 0 else 0

        # Count total upper-floor addresses across all buildings and open/closed breakdown
        total_upper_floor_addresses = sum(d.get('total_upper_floor_addresses', 0) for d in processed_data)
        open_upper_floor_addresses = sum(d.get('upper_floor_open_count', 0) for d in processed_data)
        closed_upper_floor_addresses = sum(d.get('upper_floor_closed_count', 0) for d in processed_data)

        # Count buildings by their overall status (based on streetfront addresses)
        open_buildings = len([d for d in processed_data if d['status'] == 'Open'])
        closed_buildings = len([d for d in processed_data if d['status'] == 'Closed'])
        unknown_buildings = len([d for d in processed_data if d['status'] == 'Unknown'])

        # Group by district for district-level analysis
        district_stats = {}
        for record in processed_data:
            district = record['supervisor_district'] or 'Unknown'
            if district not in district_stats:
                district_stats[district] = {'total': 0, 'open': 0, 'closed': 0, 'unknown': 0}

            district_stats[district]['total'] += 1
            if record['status'] == 'Open':
                district_stats[district]['open'] += 1
            elif record['status'] == 'Closed':
                district_stats[district]['closed'] += 1
            else:
                district_stats[district]['unknown'] += 1

        # Calculate vacancy rates by district
        for district in district_stats:
            stats = district_stats[district]
            total_in_district = stats['open'] + stats['closed']  # Only count known statuses
            stats['vacancy_rate'] = (stats['closed'] / total_in_district * 100) if total_in_district > 0 else 0
        
        
        return JSONResponse({
            "status": "success",
            "data": processed_data,
            "summary": {
                "total_buildings": total_buildings,
                "open_buildings": open_buildings,
                "closed_buildings": closed_buildings,
                "unknown_buildings": unknown_buildings,
                "total_streetfront_addresses": total_streetfront_addresses,
                "upper_floor_addresses": total_upper_floor_addresses,
                "open_streetfront_addresses": open_streetfront_addresses,
                "closed_streetfront_addresses": closed_streetfront_addresses,
                "open_upper_floor_addresses": open_upper_floor_addresses,
                "closed_upper_floor_addresses": closed_upper_floor_addresses,
                "streetfront_vacancy_rate": round(streetfront_vacancy_rate, 2),
                "total_businesses": total_streetfront_addresses + total_upper_floor_addresses  # Businesses = Storefronts + Upper Floor
            },
            "district_stats": district_stats,
            "filters_applied": {
                "industry_type": list(industry_type) if isinstance(industry_type, list) else str(industry_type) if industry_type is not None else None,
                "license_type": list(license_type) if isinstance(license_type, list) else str(license_type) if license_type is not None else None,
                "business_corridor": list(business_corridor) if isinstance(business_corridor, list) else str(business_corridor) if business_corridor is not None else None,
                "corridor_only": corridor_only,
                "district": list(district) if isinstance(district, list) else str(district) if district is not None else None,
                "business_search": business_search
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_vacancy_data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting vacancy data: {str(e)}")

@router.get("/api/vacancy/cached")
async def get_cached_vacancy_data(
    business_search: Optional[str] = Query(None, description="Business name to search for"),
    industry_type: Optional[List[str]] = Query(None),
    license_type: Optional[List[str]] = Query(None),
    business_corridor: Optional[List[str]] = Query(None),
    corridor_only: bool = Query(False, description="Filter to only businesses with corridor designation"),
    district: Optional[List[str]] = Query(None),
    zoning_district: Optional[List[str]] = Query(None, description="Filter by zoning district"),
    status: Optional[str] = Query(None),
    street_level_only: bool = Query(False, description="Filter to only street-level storefronts"),
    licensed_only: bool = Query(False, description="Filter to only licensed businesses"),
    commercial_tax_filing_only: bool = Query(False, description="Filter to only buildings with commercial tax filings"),
    limit: int = Query(250000, description="Maximum number of results to return")
):
    """Get vacancy data from local cache - much faster than API calls"""
    try:
        import json
        from ai.tools.db_utils import execute_with_connection
        
        logger.info(f"Getting cached vacancy data with search: {business_search}")
        
        # Check if NEW simple cache tables exist
        def check_cache_exists(conn):
            cursor = conn.cursor()
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'business_registrations_cache'
                );
            """)
            exists = cursor.fetchone()[0]
            cursor.close()
            return exists
        
        cache_result = execute_with_connection(check_cache_exists)
        cache_exists = cache_result.get('result', False) if cache_result.get('status') == 'success' else False
        
        if not cache_exists:
            logger.warning("Simple cache tables don't exist. Falling back to API endpoint.")
            # Fall back to the original API endpoint
            result = await get_vacancy_data(
                industry_type=industry_type,
                license_type=license_type,
                business_corridor=business_corridor,
                district=district,
                business_search=business_search,
                limit=limit
            )
            # Add a note about cache setup
            if hasattr(result, 'body') and result.body:
                data = json.loads(result.body)
                data['cache_note'] = "Using API data. Refresh cache to enable fast cached searches."
                return JSONResponse(data)
            return result
        
        # Build SQL query for simple raw cache - single table query (no joins needed)
        # The has_commercial_tax_filing flag is pre-computed during cache refresh
        base_query = """
        SELECT 
            b.id, b.certificate_number, b.dba_name, b.ownership_name, b.full_business_address,
            b.normalized_address, b.is_street_level, b.has_commercial_tax_filing,
            b.dba_start_date, b.dba_end_date, b.location_start_date, b.location_end_date,
            b.administratively_closed, b.naic_code_description, b.lic_code_description,
            b.business_corridor, b.supervisor_district, b.zoning_district, b.location_lat, b.location_lon
        FROM business_registrations_cache b
        WHERE 1=1
        """
        
        params = []
        
        # Add business search filter - search business name, ownership name, and address
        if business_search and business_search.strip():
            base_query += " AND (b.dba_name ILIKE %s OR b.ownership_name ILIKE %s OR b.full_business_address ILIKE %s)"
            search_term = f"%{business_search.strip()}%"
            params.append(search_term)
            params.append(search_term)
            params.append(search_term)
        
        # Add industry type filter
        if industry_type:
            if isinstance(industry_type, list):
                industry_conditions = " OR ".join([f"b.naic_code_description ILIKE %s" for _ in industry_type])
                base_query += f" AND ({industry_conditions})"
                for item in industry_type:
                    params.append(f"%{item}%")
            else:
                base_query += " AND b.naic_code_description ILIKE %s"
                params.append(f"%{industry_type}%")
        
        # Add license type filter
        if license_type:
            if isinstance(license_type, list):
                license_conditions = " OR ".join([f"b.lic_code_description ILIKE %s" for _ in license_type])
                base_query += f" AND ({license_conditions})"
                for item in license_type:
                    params.append(f"%{item}%")
            else:
                base_query += " AND b.lic_code_description ILIKE %s"
                params.append(f"%{license_type}%")
        
        # Add business corridor filter
        if business_corridor:
            if isinstance(business_corridor, list):
                corridor_conditions = " OR ".join([f"b.business_corridor ILIKE %s" for _ in business_corridor])
                base_query += f" AND ({corridor_conditions})"
                for item in business_corridor:
                    params.append(f"%{item}%")
            else:
                base_query += " AND b.business_corridor ILIKE %s"
                params.append(f"%{business_corridor}%")
        
        # Add corridor only filter
        if corridor_only:
            base_query += " AND b.business_corridor IS NOT NULL AND b.business_corridor != ''"
        
        # Add district filter
        if district:
            if isinstance(district, list):
                district_conditions = " OR ".join([f"b.supervisor_district = %s" for _ in district])
                base_query += f" AND ({district_conditions})"
                for item in district:
                    params.append(str(item))
            else:
                base_query += " AND b.supervisor_district = %s"
                params.append(str(district))
        
        # Add zoning district filter
        if zoning_district:
            if isinstance(zoning_district, list):
                zoning_conditions = " OR ".join([f"b.zoning_district = %s" for _ in zoning_district])
                base_query += f" AND ({zoning_conditions})"
                for item in zoning_district:
                    params.append(str(item))
            else:
                base_query += " AND b.zoning_district = %s"
                params.append(str(zoning_district))
        
        # Status filter will be applied in Python after determining status from dates
        
        # Add street-level only filter
        if street_level_only:
            base_query += " AND b.is_street_level = true"
        
        # Add licensed only filter
        if licensed_only:
            base_query += " AND b.lic_code_description IS NOT NULL AND b.lic_code_description != ''"
        
        # Add commercial tax filing filter (uses pre-computed flag)
        if commercial_tax_filing_only:
            base_query += " AND b.has_commercial_tax_filing = true"
        
        # Add ordering: Sort by dba_name for consistent results
        base_query += " ORDER BY b.dba_name"
        base_query += f" LIMIT {min(limit, 250000)}"
        
        logger.info(f"Executing cached query: {base_query}")
        logger.info(f"With params: {params}")
        logger.info(f"Filters applied - corridor_only: {corridor_only}, street_level_only: {street_level_only}, "
                   f"licensed_only: {licensed_only}, commercial_tax_filing_only: {commercial_tax_filing_only}, "
                   f"zoning_district: {zoning_district}")
        
        def get_cached_data(conn):
            cursor = conn.cursor()
            cursor.execute(base_query, params)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            cursor.close()
            return [dict(zip(columns, row)) for row in rows]
        
        cached_result = execute_with_connection(get_cached_data)
        raw_business_data = cached_result.get('result', []) if cached_result.get('status') == 'success' else []
        
        logger.info(f"Raw business data count: {len(raw_business_data)}")
        if raw_business_data and len(raw_business_data) > 0:
            logger.info(f"Sample record keys: {list(raw_business_data[0].keys())}")
            logger.info(f"Sample has_commercial_tax_filing: {raw_business_data[0].get('has_commercial_tax_filing')}")
        
        # Process raw data in Python - determine status, group by normalized address, etc.
        from datetime import datetime
        from collections import defaultdict
        from decimal import Decimal
        
        # Helper function to determine business status from dates
        def determine_status(record):
            """Determine if a business is Open or Closed based on dates"""
            dba_start = record.get('dba_start_date')
            dba_end = record.get('dba_end_date')
            location_start = record.get('location_start_date')
            location_end = record.get('location_end_date')
            admin_closed = record.get('administratively_closed', False)
            
            # Closed if administratively closed
            if admin_closed:
                return 'Closed'
            
            # Normalize dates for comparison
            def normalize_date(d):
                if d is None:
                    return None
                if hasattr(d, 'date'):
                    return d.date()
                return d
            
            dba_start_date = normalize_date(dba_start)
            dba_end_date = normalize_date(dba_end)
            location_start_date = normalize_date(location_start)
            location_end_date = normalize_date(location_end)
            
            # Closed if opened and closed on the same day (likely data error or never actually opened)
            if dba_start_date and dba_end_date and dba_start_date == dba_end_date:
                    return 'Closed'
            
            if location_start_date and location_end_date and location_start_date == location_end_date:
                    return 'Closed'
            
            # Get the most recent start and end dates
            all_start_dates = [d for d in [dba_start_date, location_start_date] if d is not None]
            all_end_dates = [d for d in [dba_end_date, location_end_date] if d is not None]
            
            most_recent_start = max(all_start_dates) if all_start_dates else None
            most_recent_end = max(all_end_dates) if all_end_dates else None
            
            # If there's an end date, check if it's more recent than the start date
            if most_recent_end:
                # If no start date, or end date is more recent than start date, it's closed
                if most_recent_start is None or most_recent_end >= most_recent_start:
                    return 'Closed'
                # If start date is more recent than end date, it reopened and is open
                else:
                    return 'Open'
            
            # If there's a start date and no end date, it's open
            if most_recent_start:
                return 'Open'
            
            # No dates available - default to open (assume active if no closure info)
            return 'Open'
        
        # Helper function to check if address is street level
        def is_street_level(address):
            """
            Check if address is street-level (not upper floor).
            Uses updated logic: all units with numbers are upper floor.
            Letters (A, B, C, etc.) are ground floor.
            """
            if not address:
                return True
            import re
            s = address.upper()
            
            # Indicators of units/floors
            if re.search(r'#\s*[0-9]+', s):
                return False
            if re.search(r'\b(APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM)\b\s*[A-Z0-9]*', s):
                return False
            if re.search(r'\b(FL|FLOOR)\b\s*[0-9A-Z]*', s):
                return False
            if re.search(r'\b(2ND|3RD|4TH|5TH|6TH|7TH|8TH|9TH|10TH|11TH|12TH)\b', s):
                return False
            
            # Parse address to extract unit component
            street_number, street_name, unit = parse_address_components(address)
            
            # If unit exists and contains any digits, it's upper floor
            if unit:
                if re.search(r'\d', unit):
                    return False
                if re.search(r'[A-Z]\d|\d[A-Z]', unit):
                    return False
            
            # Trailing numeric token - all numeric units are now considered upper floor
            if re.search(r'\s([0-9]+)\s*$', s):
                return False
            
            if re.search(r'\s[0-9]+[A-Z]\s*$', s):
                return False
            
            if re.search(r'\s\d+[-_]\d+', s):
                return False
            
            return True
        
        def canonicalize_unit_address(full_address):
            """
            Canonicalize unit addresses to handle variations like:
            - '2139 Polk St A' and '2139 A Polk St' -> '2139 POLK ST A'
            - '2139 Polk St #C' -> '2139 POLK ST C'
            - '2139 B Polk St B' -> '2139 POLK ST B' (take last unit letter)
            """
            import re
            if not full_address:
                return ''
            
            addr = full_address.strip().upper()
            
            # Pattern to match: [number] [optional unit] [street name] [optional unit]
            # Examples: "2139 A POLK ST", "2139 POLK ST A", "2139 POLK ST #C"
            
            # Extract street number at the start
            match = re.match(r'^(\d+(?:-\d+)?)', addr)
            if not match:
                return addr  # Can't parse, return as-is
            
            street_number = match.group(1)
            rest = addr[len(street_number):].strip()
            
            # Try to extract unit identifier (single letter/number or #X format)
            # Look for patterns like: "A ", "#C", "B ", at beginning or end
            unit_letters = []
            
            # Pattern 1: Unit letter at beginning (before street name)
            # "A POLK ST" or "#C POLK ST"
            unit_match = re.match(r'^([A-Z]|#[A-Z0-9]+)\s+(.+)$', rest)
            if unit_match:
                unit = unit_match.group(1).replace('#', '').strip()
                rest = unit_match.group(2)
                unit_letters.append(unit)
            
            # Pattern 2: Unit letter at end (after street name)
            # "POLK ST A" or "POLK ST #C" or "POLK ST B"
            unit_match = re.search(r'\s+([A-Z]|#[A-Z0-9]+)$', rest)
            if unit_match:
                unit = unit_match.group(1).replace('#', '').strip()
                rest = rest[:unit_match.start()]
                unit_letters.append(unit)
            
            # Clean up street name
            street_name = rest.strip()
            
            # Normalize street name
            street_name = re.sub(r'\s+', ' ', street_name)
            
            # Canonical format: "NUMBER STREET NAME UNIT"
            # If we found multiple unit letters (like "2139 B POLK ST B"), take the last one
            if unit_letters:
                # Take the last unit letter (most likely correct)
                canonical = f"{street_number} {street_name} {unit_letters[-1]}"
            else:
                canonical = f"{street_number} {street_name}"
            
            return canonical.strip()
        
        # Group by normalized building address (use pre-computed DB column)
        building_groups = defaultdict(list)
        for record in raw_business_data:
            # Use pre-computed normalized_address from database (more reliable)
            normalized_address = record.get('normalized_address', '')
            if normalized_address:
                building_groups[normalized_address].append(record)
        
        logger.info(f"Grouped into {len(building_groups)} buildings")
        
        # Process each building group
        processed_data = []
        for normalized_address, business_records in building_groups.items():
            # FIRST: Group businesses by their individual distinct addresses within this building
            # This is critical - multiple businesses can be at the same unit address over time
            individual_address_groups = defaultdict(list)
            for record in business_records:
                raw_address = record.get('full_business_address', '')
                # Canonicalize address to handle variations like "2139 A Polk St" vs "2139 Polk St A"
                canonical_address = canonicalize_unit_address(raw_address)
                individual_address_groups[canonical_address].append(record)
            
            # NOW process each distinct address
            addresses_data_formatted = []
            street_level_open = 0
            street_level_closed = 0
            upper_floor_open = 0
            upper_floor_closed = 0
            
            # Get coordinates from first record with coordinates
            coordinates = [None, None]
            
            for distinct_address, address_businesses in individual_address_groups.items():
                if not address_businesses:
                    continue
                
                # Get the MOST RECENT business at this address (for display purposes)
                # Use MAXIMUM of dba_start_date and location_start_date (same as building-level logic)
                most_recent_business = max(address_businesses,
                    key=lambda r: max(
                        r.get('dba_start_date') or datetime.min,
                        r.get('location_start_date') or datetime.min
                    ))
                
                # Check ALL businesses at this address - if ANY is open, address is open
                # This handles cases where one business closes but another is still open
                address_has_open_business = False
                address_has_closed_business = False
                
                for business in address_businesses:
                    business_status = determine_status(business)
                    if business_status == 'Open':
                        address_has_open_business = True
                    else:
                        address_has_closed_business = True
                
                # Determine status for this ADDRESS: open if ANY business is open
                if address_has_open_business:
                    unit_status = 'Open'
                elif address_has_closed_business:
                    unit_status = 'Closed'
                else:
                    unit_status = 'Unknown'
                
                # Check if this ADDRESS is street level (use database column from cache)
                # But also validate using the address string to catch any database inconsistencies
                db_street_level = most_recent_business.get('is_street_level', True)
                address_string = distinct_address or most_recent_business.get('full_business_address', '')
                
                # Validate with runtime check - override database value if needed
                validated_street_level = is_street_level(address_string)
                
                # Use validated value if it differs from database (log if different for debugging)
                if db_street_level != validated_street_level:
                    logger.debug(f"Street level mismatch for {address_string}: DB={db_street_level}, Validated={validated_street_level}")
                
                street_level = validated_street_level
                
                # Count THIS ADDRESS (not each business)
                if street_level:
                    if unit_status == 'Open':
                        street_level_open += 1
                    else:
                        street_level_closed += 1
                else:
                    if unit_status == 'Open':
                        upper_floor_open += 1
                    else:
                        upper_floor_closed += 1
                
                # Get coordinates if we don't have them yet
                if coordinates == [None, None]:
                    lat = most_recent_business.get('location_lat')
                    lon = most_recent_business.get('location_lon')
                    if lat is not None and lon is not None:
                        # Convert Decimal to float if needed
                        lat = float(lat) if isinstance(lat, Decimal) else lat
                        lon = float(lon) if isinstance(lon, Decimal) else lon
                        coordinates = [lon, lat]
                
                # Convert businesses to JSON-serializable format
                businesses_serializable = []
                for biz in address_businesses:
                    biz_data = {
                        'dba_name': biz.get('dba_name', 'Unknown Business'),
                        'certificate_number': biz.get('certificate_number'),
                        'dba_start_date': biz.get('dba_start_date').isoformat() if biz.get('dba_start_date') else None,
                        'dba_end_date': biz.get('dba_end_date').isoformat() if biz.get('dba_end_date') else None,
                        'location_start_date': biz.get('location_start_date').isoformat() if biz.get('location_start_date') else None,
                        'location_end_date': biz.get('location_end_date').isoformat() if biz.get('location_end_date') else None,
                        'naic_code_description': biz.get('naic_code_description'),
                        'lic_code_description': biz.get('lic_code_description'),
                        'status': determine_status(biz),
                        'ban_match': None,  # No longer joined to tax table
                        'address_match': None,  # No longer joined to tax table
                        'tax_filing_year': None,
                        'tax_filing_entity': None,
                        'vacancy_status': None
                    }
                    businesses_serializable.append(biz_data)
                
                # Format this address for frontend
                formatted_unit = {
                    'address': distinct_address,
                    'distinct_address': distinct_address,
                    'status': unit_status,
                    'unit_status': unit_status,  # Backwards compatibility
                    'street_level': street_level,  # Frontend expects 'street_level' not 'is_street_level'
                    'is_street_level': street_level,  # Keep both for compatibility
                    'business_count': len(address_businesses),  # Historical count
                    'dba_name': most_recent_business.get('dba_name', 'Unknown Business'),
                    'open_date': most_recent_business.get('dba_start_date').isoformat() if most_recent_business.get('dba_start_date') else None,
                    'close_date': most_recent_business.get('dba_end_date').isoformat() if most_recent_business.get('dba_end_date') else None,
                    'businesses': businesses_serializable  # All businesses at this address (JSON-safe)
                }
                addresses_data_formatted.append(formatted_unit)
            
            # Determine overall building status - Open if ANY address has ANY open business
            all_businesses_flat = []
            for businesses in individual_address_groups.values():
                all_businesses_flat.extend(businesses)
            
            # Check if ANY business at ANY address is open
            building_has_open_business = False
            building_has_closed_business = False
            
            for business in all_businesses_flat:
                business_status = determine_status(business)
                if business_status == 'Open':
                    building_has_open_business = True
                else:
                    building_has_closed_business = True
            
            # Building is Open if ANY business is open
            if building_has_open_business:
                building_status = 'Open'
            elif building_has_closed_business:
                building_status = 'Closed'
            else:
                building_status = 'Unknown'
            
            # Get most recent business for display purposes
            most_recent = max(all_businesses_flat, 
                            key=lambda r: max(
                                r.get('dba_start_date') or datetime.min,
                                r.get('location_start_date') or datetime.min
                            ))
            
            # Get representative data from first record
            first_record = business_records[0]
            
            # Check if building has tax filing (uses pre-computed flag)
            has_tax_filing = first_record.get('has_commercial_tax_filing', False)
            
            # Apply status filter if specified
            if status and building_status != status:
                continue
            
            # Create building record
            building_record = {
                'location': {
                    'type': 'Point',
                    'coordinates': coordinates
                },
                'dba_name': most_recent.get('dba_name', 'Unknown Business'),
                'naic_code_description': first_record['naic_code_description'],
                'lic_code_description': first_record['lic_code_description'],
                'business_corridor': first_record['business_corridor'],
                'supervisor_district': first_record['supervisor_district'],
                'zoning_district': first_record.get('zoning_district'),  # Add zoning district
                'status': building_status,
                'full_business_address': normalized_address,
                'distinct_address': normalized_address,
                'building_address': normalized_address,
                'business_count': len(all_businesses_flat),  # Total businesses across all addresses
                'streetfront_open_count': street_level_open,
                'streetfront_closed_count': street_level_closed,
                'total_streetfront_addresses': street_level_open + street_level_closed,
                'upper_floor_open_count': upper_floor_open,
                'upper_floor_closed_count': upper_floor_closed,
                'total_upper_floor_addresses': upper_floor_open + upper_floor_closed,
                'addresses_data': addresses_data_formatted,
                'individual_addresses_count': len(individual_address_groups),  # Number of DISTINCT addresses
                'has_commercial_tax_filing': has_tax_filing,
                'tax_filing_addresses': [normalized_address] if has_tax_filing else [],
                'vacancy_status': None,  # No longer available (would need separate tax table query)
                'tax_filing_year': None,
                'tax_filing_ban': None,
                'tax_filing_entity': None
            }
            processed_data.append(building_record)
        
        # Group buildings by coordinates (combine buildings at same location)
        logger.info(f"Grouping {len(processed_data)} buildings by coordinates...")
        coordinate_groups = defaultdict(list)
        buildings_without_coords = []
        
        for building in processed_data:
            coords = building.get('location', {}).get('coordinates', [])
            # Check that coordinates exist and are not None
            if len(coords) == 2 and coords[0] is not None and coords[1] is not None:
                # Round to 6 decimal places to group nearby buildings
                coord_key = (round(coords[0], 6), round(coords[1], 6))
                coordinate_groups[coord_key].append(building)
            else:
                # Buildings without valid coordinates are kept separately
                buildings_without_coords.append(building)
        
        # Merge buildings at same location
        merged_data = []
        for coord_key, buildings in coordinate_groups.items():
            if len(buildings) == 1:
                # Single building at this location - keep as is
                merged_data.append(buildings[0])
            else:
                # Multiple buildings at same location - merge them
                logger.info(f"Merging {len(buildings)} buildings at {coord_key}")
                
                # Combine all addresses_data from all buildings, merging addresses with same canonical address
                all_addresses_data_dict = {}  # Key: canonical address, Value: merged address data
                all_business_names = []
                total_business_count = 0
                total_streetfront_open = 0
                total_streetfront_closed = 0
                total_streetfront = 0
                total_upper_open = 0
                total_upper_closed = 0
                total_upper = 0
                
                for bldg in buildings:
                    # Process each address from this building
                    for addr_data in bldg.get('addresses_data', []):
                        # Use distinct_address as the key for merging (canonicalized address)
                        canonical_key = addr_data.get('distinct_address', addr_data.get('address', ''))
                        
                        if canonical_key in all_addresses_data_dict:
                            # Merge businesses from this address into existing address entry
                            existing_addr = all_addresses_data_dict[canonical_key]
                            existing_businesses = existing_addr.get('businesses', [])
                            new_businesses = addr_data.get('businesses', [])
                            
                            # Merge businesses (avoid duplicates by certificate_number)
                            existing_cert_nums = {b.get('certificate_number') for b in existing_businesses if b.get('certificate_number')}
                            for biz in new_businesses:
                                if biz.get('certificate_number') not in existing_cert_nums:
                                    existing_businesses.append(biz)
                            
                            # Update status - if either is open, merged address is open
                            if existing_addr.get('status') == 'Open' or addr_data.get('status') == 'Open':
                                existing_addr['status'] = 'Open'
                            elif existing_addr.get('status') == 'Closed' and addr_data.get('status') == 'Closed':
                                existing_addr['status'] = 'Closed'
                            else:
                                existing_addr['status'] = 'Unknown'
                            
                            # Update business count
                            existing_addr['business_count'] = len(existing_businesses)
                            
                            # Update dba_name to show most recent business
                            if existing_businesses:
                                # Find most recent business by dates
                                def parse_date_str(date_str):
                                    """Parse date string to datetime for comparison"""
                                    if not date_str:
                                        return datetime.min
                                    try:
                                        if isinstance(date_str, str):
                                            # Handle ISO format strings
                                            if 'T' in date_str:
                                                return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                                            return datetime.strptime(date_str, '%Y-%m-%d')
                                        return datetime.min
                                    except:
                                        return datetime.min
                                
                                most_recent = max(existing_businesses,
                                    key=lambda b: max(
                                        parse_date_str(b.get('dba_start_date')),
                                        parse_date_str(b.get('location_start_date'))
                                    ))
                                existing_addr['dba_name'] = most_recent.get('dba_name', existing_addr.get('dba_name', 'Unknown Business'))
                        else:
                            # First time seeing this canonical address - add it
                            all_addresses_data_dict[canonical_key] = addr_data.copy()
                    
                    all_business_names.append(bldg.get('dba_name', 'Unknown'))
                    total_business_count += bldg.get('business_count', 0)
                    total_streetfront_open += bldg.get('streetfront_open_count', 0)
                    total_streetfront_closed += bldg.get('streetfront_closed_count', 0)
                    total_streetfront += bldg.get('total_streetfront_addresses', 0)
                    total_upper_open += bldg.get('upper_floor_open_count', 0)
                    total_upper_closed += bldg.get('upper_floor_closed_count', 0)
                    total_upper += bldg.get('total_upper_floor_addresses', 0)
                
                # Convert dict back to list
                all_addresses_data = list(all_addresses_data_dict.values())
                
                # Determine overall status based on majority
                open_count = sum(1 for b in buildings if b.get('status') == 'Open')
                closed_count = sum(1 for b in buildings if b.get('status') == 'Closed')
                overall_status = 'Open' if open_count > closed_count else ('Closed' if closed_count > 0 else 'Unknown')
                
                # Create combined building record
                primary_building = buildings[0]
                # Get zoning district from primary building (or use first non-null one)
                zoning_districts = [b.get('zoning_district') for b in buildings if b.get('zoning_district')]
                primary_zoning = zoning_districts[0] if zoning_districts else primary_building.get('zoning_district')
                
                merged_building = {
                    'location': primary_building.get('location'),
                    'dba_name': f"{len(buildings)} Buildings: {', '.join(all_business_names[:3])}{'...' if len(all_business_names) > 3 else ''}",
                    'naic_code_description': 'Mixed',
                    'lic_code_description': 'Mixed',
                    'business_corridor': primary_building.get('business_corridor'),
                    'supervisor_district': primary_building.get('supervisor_district'),
                    'zoning_district': primary_zoning,  # Use primary building's zoning district
                    'status': overall_status,
                    'full_business_address': f"{buildings[0].get('building_address')} + {len(buildings)-1} more",
                    'distinct_address': buildings[0].get('distinct_address'),
                    'building_address': buildings[0].get('building_address'),
                    'business_count': total_business_count,
                    'streetfront_open_count': total_streetfront_open,
                    'streetfront_closed_count': total_streetfront_closed,
                    'total_streetfront_addresses': total_streetfront,
                    'upper_floor_open_count': total_upper_open,
                    'upper_floor_closed_count': total_upper_closed,
                    'total_upper_floor_addresses': total_upper,
                    'addresses_data': all_addresses_data,
                    'individual_addresses_count': len(all_addresses_data),
                    'has_commercial_tax_filing': any(b.get('has_commercial_tax_filing', False) for b in buildings),
                    'tax_filing_addresses': [],
                    'vacancy_status': None,
                    'tax_filing_year': None,
                    'tax_filing_ban': None,
                    'tax_filing_entity': None,
                    'buildings_at_location': len(buildings),  # Add this for scaling
                }
                merged_data.append(merged_building)
        
        # Add buildings without coordinates to the result (can't be grouped by location)
        merged_data.extend(buildings_without_coords)
        
        logger.info(f"After merging: {len(merged_data)} unique locations (from {len(processed_data)} buildings)")
        logger.info(f"  - {len(coordinate_groups)} locations with coordinates")
        logger.info(f"  - {len(buildings_without_coords)} buildings without valid coordinates")
        processed_data = merged_data
        
        # Calculate summary statistics
        total_buildings = len(processed_data)
        open_buildings = len([d for d in processed_data if d['status'] == 'Open'])
        closed_buildings = len([d for d in processed_data if d['status'] == 'Closed'])
        unknown_buildings = len([d for d in processed_data if d['status'] == 'Unknown'])
        
        total_streetfront_addresses = sum(d['total_streetfront_addresses'] for d in processed_data)
        open_streetfront_addresses = sum(d['streetfront_open_count'] for d in processed_data)
        closed_streetfront_addresses = sum(d['streetfront_closed_count'] for d in processed_data)
        streetfront_vacancy_rate = (closed_streetfront_addresses / total_streetfront_addresses * 100) if total_streetfront_addresses > 0 else 0
        
        total_upper_floor_addresses = sum(d.get('total_upper_floor_addresses', 0) for d in processed_data)
        open_upper_floor_addresses = sum(d.get('upper_floor_open_count', 0) for d in processed_data)
        closed_upper_floor_addresses = sum(d.get('upper_floor_closed_count', 0) for d in processed_data)
        
        # Group by district for district-level analysis
        district_stats = {}
        for record in processed_data:
            district = record['supervisor_district'] or 'Unknown'
            if district not in district_stats:
                district_stats[district] = {'total': 0, 'open': 0, 'closed': 0, 'unknown': 0}
            
            district_stats[district]['total'] += 1
            if record['status'] == 'Open':
                district_stats[district]['open'] += 1
            elif record['status'] == 'Closed':
                district_stats[district]['closed'] += 1
            else:
                district_stats[district]['unknown'] += 1
        
        # Calculate vacancy rates by district
        for district in district_stats:
            stats = district_stats[district]
            total_in_district = stats['open'] + stats['closed']
            stats['vacancy_rate'] = (stats['closed'] / total_in_district * 100) if total_in_district > 0 else 0
        
        return JSONResponse({
            "status": "success",
            "data": processed_data,
            "summary": {
                "total_buildings": total_buildings,
                "open_buildings": open_buildings,
                "closed_buildings": closed_buildings,
                "unknown_buildings": unknown_buildings,
                "total_streetfront_addresses": total_streetfront_addresses,
                "upper_floor_addresses": total_upper_floor_addresses,
                "open_streetfront_addresses": open_streetfront_addresses,
                "closed_streetfront_addresses": closed_streetfront_addresses,
                "open_upper_floor_addresses": open_upper_floor_addresses,
                "closed_upper_floor_addresses": closed_upper_floor_addresses,
                "streetfront_vacancy_rate": round(streetfront_vacancy_rate, 2),
                "total_businesses": total_streetfront_addresses + total_upper_floor_addresses  # Businesses = Storefronts + Upper Floor
            },
            "district_stats": district_stats,
            "filters_applied": {
                "business_search": business_search,
                "industry_type": list(industry_type) if isinstance(industry_type, list) else str(industry_type) if industry_type is not None else None,
                "license_type": list(license_type) if isinstance(license_type, list) else str(license_type) if license_type is not None else None,
                "business_corridor": list(business_corridor) if isinstance(business_corridor, list) else str(business_corridor) if business_corridor is not None else None,
                "district": list(district) if isinstance(district, list) else str(district) if district is not None else None,
                "status": status
            },
            "source": "cached"
        })
        
    except Exception as e:
        logger.error(f"Error in get_cached_vacancy_data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting cached vacancy data: {str(e)}")

@router.get("/api/vacancy/search")
async def search_businesses(
    business_search: str = Query(..., description="Business name to search for"),
    limit: int = Query(50, description="Maximum number of results to return")
):
    """Fast business search endpoint - optimized for quick business name searches"""
    try:
        if not business_search or len(business_search.strip()) < 2:
            raise HTTPException(status_code=400, detail="Business search term must be at least 2 characters")
        
        search_term = business_search.strip()
        logger.info(f"Fast business search for: '{search_term}'")
        
        # Build optimized query for business search
        # Note: SOQL uses case-sensitive LIKE, so we convert both to uppercase for case-insensitive search
        search_term_upper = search_term.upper()
        search_query = f"""
        SELECT 
            location,
            dba_name,
            ownership_name,
            naic_code_description,
            lic_code_description,
            business_corridor,
            supervisor_district,
            dba_start_date,
            location_start_date,
            dba_end_date,
            location_end_date,
            administratively_closed,
            full_business_address,
            certificate_number
        WHERE location IS NOT NULL 
        AND (upper(dba_name) LIKE '%{search_term_upper}%' 
             OR upper(ownership_name) LIKE '%{search_term_upper}%'
             OR upper(full_business_address) LIKE '%{search_term_upper}%')
        ORDER BY dba_name
        LIMIT {min(limit, 1000)}
        """
        
        logger.info(f"Executing fast search query: {search_query}")
        
        # Fetch data from DataSF API
        from ai.tools.data_fetcher import fetch_data_from_api
        
        query_object = {
            'endpoint': 'g8m3-pdis',
            'query': search_query
        }
        
        result = fetch_data_from_api(query_object)
        
        if not result or 'data' not in result:
            error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
            raise HTTPException(status_code=500, detail=f"Failed to fetch search data: {error_msg}")
        
        raw_data = result['data']
        logger.info(f"Found {len(raw_data)} businesses matching '{search_term}'")
        
        # Process results for map display (simplified processing for speed)
        processed_data = []
        
        def parse_location_coordinates(location_data):
            """Extract coordinates from location field"""
            coordinates = [0, 0]  # Default coordinates
            
            if location_data:
                if isinstance(location_data, dict):
                    if 'coordinates' in location_data:
                        coordinates = location_data['coordinates']
                    elif 'longitude' in location_data and 'latitude' in location_data:
                        coordinates = [location_data['longitude'], location_data['latitude']]
                elif isinstance(location_data, str) and location_data.startswith('POINT'):
                    try:
                        coords_str = location_data.replace('POINT (', '').replace(')', '')
                        lon, lat = coords_str.split()
                        coordinates = [float(lon), float(lat)]
                    except (ValueError, IndexError):
                        pass
            
            return coordinates
        
        for record in raw_data:
            coordinates = parse_location_coordinates(record.get('location'))
            
            # Determine business status (simplified)
            dba_start = record.get('dba_start_date')
            location_start = record.get('location_start_date')
            dba_end = record.get('dba_end_date')
            location_end = record.get('location_end_date')
            admin_closed = record.get('administratively_closed', False)
            
            # Simple status determination
            if admin_closed:
                status = 'Closed'
            elif dba_end or location_end:
                status = 'Closed'
            else:
                status = 'Open'
            
            processed_record = {
                'location': {
                    'type': 'Point',
                    'coordinates': coordinates
                },
                'dba_name': record.get('dba_name', 'Unknown Business'),
                'ownership_name': record.get('ownership_name', 'Unknown Owner'),
                'naic_code_description': record.get('naic_code_description'),
                'lic_code_description': record.get('lic_code_description'),
                'business_corridor': record.get('business_corridor'),
                'supervisor_district': record.get('supervisor_district'),
                'status': status,
                'full_business_address': record.get('full_business_address'),
                'distinct_address': record.get('full_business_address'),
                'building_address': record.get('full_business_address'),
                'business_count': 1,
                'streetfront_open_count': 1 if status == 'Open' else 0,
                'streetfront_closed_count': 1 if status == 'Closed' else 0,
                'total_streetfront_addresses': 1,
                'upper_floor_open_count': 0,
                'upper_floor_closed_count': 0,
                'total_upper_floor_addresses': 0,
                'addresses_data': [{
                    'address': record.get('full_business_address'),
                    'status': status,
                    'is_street_level': True,
                    'business_count': 1,
                    'businesses': [record]
                }],
                'individual_addresses_count': 1
            }
            
            processed_data.append(processed_record)
        
        # Calculate summary statistics
        total_businesses = len(processed_data)
        open_businesses = len([b for b in processed_data if b['status'] == 'Open'])
        closed_businesses = len([b for b in processed_data if b['status'] == 'Closed'])
        
        return JSONResponse({
            "status": "success",
            "data": processed_data,
            "summary": {
                "total_businesses": total_businesses,
                "open_businesses": open_businesses,
                "closed_businesses": closed_businesses,
                "search_term": search_term
            },
            "filters_applied": {
                "business_search": search_term,
                "limit": limit
            }
        })
        
    except Exception as e:
        logger.error(f"Error in search_businesses: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error searching businesses: {str(e)}")

@router.post("/api/vacancy/refresh-cache")
async def refresh_business_cache(
    background_tasks: BackgroundTasks,
    limit: Optional[int] = Query(None, description="Limit number of business records to process")
):
    """Refresh the business cache by fetching fresh data from DataSF API (async job)"""
    try:
        logger.info("Starting business cache refresh job...")
        
        # Create a background job
        job_id = job_manager.create_job("business_cache_refresh", f"Refresh business cache (limit: {limit or 'all'})")
        logger.info(f"Created job {job_id} for business cache refresh")
        
        # Verify job was created
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Failed to create job {job_id}")
            raise HTTPException(status_code=500, detail="Failed to create background job")
        
        logger.info(f"Job {job_id} verified, scheduling background task")
        
        # Use FastAPI BackgroundTasks to ensure the task runs
        # This is more reliable than asyncio.create_task() which might be garbage collected
        background_tasks.add_task(_run_business_cache_refresh_job, job_id, limit)
        logger.info(f"Scheduled background task for business cache refresh job {job_id}")
        
        return JSONResponse({
            "status": "success",
            "message": "Business cache refresh started",
            "job_id": job_id
        })
        
    except Exception as e:
        logger.error(f"Error starting business cache refresh job: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error starting business cache refresh: {str(e)}")

@router.post("/api/vacancy/refresh-zoning")
async def refresh_zoning_only(
    background_tasks: BackgroundTasks,
    limit: Optional[int] = Query(None, description="Limit number of zoning records to process")
):
    """Update only zoning data without refreshing business/tax caches (faster for testing)"""
    try:
        logger.info("Starting zoning-only update job...")
        
        # Create a background job
        job_id = job_manager.create_job("zoning_only_update", f"Update zoning data only (limit: {limit or 'all'})")
        logger.info(f"Created job {job_id} for zoning-only update")
        
        # Verify job was created
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Failed to create job {job_id}")
            raise HTTPException(status_code=500, detail="Failed to create background job")
        
        logger.info(f"Job {job_id} verified, scheduling background task")
        
        # Use FastAPI BackgroundTasks to ensure the task runs
        background_tasks.add_task(_run_zoning_update_job, job_id, limit)
        logger.info(f"Scheduled background task for zoning update job {job_id}")
        
        return JSONResponse({
            "status": "success",
            "message": "Zoning-only update started",
            "job_id": job_id
        })
        
    except Exception as e:
        logger.error(f"Error starting zoning update job: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error starting zoning update: {str(e)}")

async def _run_zoning_update_job(job_id: str, limit: Optional[int] = None):
    """Run the zoning-only update job in the background."""
    try:
        from ai.tools.simple_business_cache import SimpleBusinessCache
        import asyncio
        
        logger.info(f"Starting zoning-only update job {job_id} with limit {limit}")
        
        # Mark job as running
        job = job_manager.get_job(job_id)
        if job:
            job.start()
            logger.info(f"Job {job_id} started")
        else:
            logger.error(f"Job {job_id} not found when trying to start!")
            return
        
        # Run the zoning update in a thread pool to avoid blocking
        def run_zoning_update():
            try:
                logger.info(f"[Thread] Creating SimpleBusinessCache instance...")
                cache = SimpleBusinessCache()
                logger.info(f"[Thread] Calling refresh_zoning_only with limit={limit}...")
                result = cache.refresh_zoning_only(limit)
                logger.info(f"[Thread] refresh_zoning_only completed with result: {result.get('status', 'unknown')}")
                return result
            except Exception as e:
                logger.error(f"[Thread] Error in run_zoning_update: {e}", exc_info=True)
                raise
        
        # Execute in thread pool to prevent blocking
        logger.info(f"Executing zoning update in thread pool...")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_zoning_update)
        logger.info(f"Zoning update thread completed, result status: {result.get('status', 'unknown')}")
        
        # Add a small delay to ensure database transaction is committed
        await asyncio.sleep(0.5)
        
        # Complete the job with results
        job = job_manager.get_job(job_id)
        if job:
            job.complete({
                "zoning_polygons": result.get('zoning_cache', {}).get('successful', 0),
                "zoning_district_matches": result.get('zoning_district_matches', 0),
                "duration_seconds": result.get('elapsed_time', 0)
            })
            logger.info(f"✅ Zoning-only update job {job_id} completed successfully")
            logger.info(f"   Zoning polygons: {result.get('zoning_cache', {}).get('successful', 0):,}")
            logger.info(f"   Zoning matches: {result.get('zoning_district_matches', 0):,}")
        else:
            logger.error(f"Job {job_id} not found when trying to complete")
        
    except Exception as e:
        logger.error(f"Error in zoning update job {job_id}: {e}", exc_info=True)
        job = job_manager.get_job(job_id)
        if job:
            await job_manager.fail_job(job_id, str(e))

async def _run_business_cache_refresh_job(job_id: str, limit: Optional[int] = None):
    """Run the business cache refresh job in the background."""
    try:
        from ai.tools.simple_business_cache import SimpleBusinessCache
        import asyncio
        
        logger.info(f"Starting simple cache refresh job {job_id} with limit {limit}")
        
        # Mark job as running
        job = job_manager.get_job(job_id)
        if job:
            job.start()
            logger.info(f"Marked job {job_id} as running")
        else:
            logger.error(f"Job {job_id} not found when trying to start!")
            return
        
        # Run the cache refresh in a thread pool to avoid blocking
        def run_cache_refresh():
            try:
                logger.info(f"[Thread] Creating SimpleBusinessCache instance...")
                cache = SimpleBusinessCache()
                logger.info(f"[Thread] Calling refresh_cache with limit={limit}...")
                result = cache.refresh_cache(limit)
                logger.info(f"[Thread] refresh_cache completed with result: {result.get('status', 'unknown')}")
                return result
            except Exception as e:
                logger.error(f"[Thread] Error in run_cache_refresh: {e}", exc_info=True)
                raise
        
        # Execute in thread pool to prevent blocking
        logger.info(f"Executing cache refresh in thread pool...")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_cache_refresh)
        logger.info(f"Cache refresh thread completed, result status: {result.get('status', 'unknown')}")
        
        # Add a small delay to ensure database transaction is committed
        await asyncio.sleep(0.5)
        
        # Complete the job with results
        job = job_manager.get_job(job_id)
        if job:
            job.complete({
                "business_records": result.get('business_cache', {}).get('successful', 0),
                "tax_records": result.get('tax_cache', {}).get('successful', 0),
                "zoning_polygons": result.get('zoning_cache', {}).get('successful', 0),
                "zoning_district_matches": result.get('zoning_district_matches', 0),
                "duration_seconds": result.get('elapsed_time', 0)
            })
            logger.info(f"✅ Simple cache refresh job {job_id} completed successfully")
            logger.info(f"   Business records: {result.get('business_cache', {}).get('successful', 0):,}")
            logger.info(f"   Tax records: {result.get('tax_cache', {}).get('successful', 0):,}")
            logger.info(f"   Zoning polygons: {result.get('zoning_cache', {}).get('successful', 0):,}")
            logger.info(f"   Zoning matches: {result.get('zoning_district_matches', 0):,}")
        else:
            logger.error(f"Job {job_id} not found when trying to complete")
        
    except Exception as e:
        logger.error(f"❌ Simple cache refresh job {job_id} failed: {str(e)}", exc_info=True)
        job = job_manager.get_job(job_id)
        if job:
            job.fail(str(e))
            logger.info(f"Marked job {job_id} as failed")
        else:
            logger.error(f"Job {job_id} not found when trying to mark as failed")

@router.get("/api/vacancy/cache-stats")
async def get_cache_stats():
    """Get statistics about the cached business data"""
    try:
        from ai.tools.simple_business_cache import SimpleBusinessCache
        from ai.tools.db_utils import execute_with_connection
        
        cache = SimpleBusinessCache()
        raw_stats = cache.get_cache_stats()
        
        # Calculate open/closed businesses by querying the cache
        def count_business_status(conn):
            cursor = conn.cursor()
            
            # Count open businesses (no end date and not admin closed)
            cursor.execute("""
                SELECT COUNT(*) FROM business_registrations_cache 
                WHERE dba_end_date IS NULL 
                AND location_end_date IS NULL 
                AND administratively_closed = FALSE
            """)
            open_count = cursor.fetchone()[0]
            
            # Count closed businesses
            cursor.execute("""
                SELECT COUNT(*) FROM business_registrations_cache 
                WHERE dba_end_date IS NOT NULL 
                OR location_end_date IS NOT NULL 
                OR administratively_closed = TRUE
            """)
            closed_count = cursor.fetchone()[0]
            
            cursor.close()
            return {'open': open_count, 'closed': closed_count}
        
        status_result = execute_with_connection(count_business_status)
        status_counts = status_result.get('result', {'open': 0, 'closed': 0}) if status_result.get('status') == 'success' else {'open': 0, 'closed': 0}
        
        # Format stats for UI (matching expected format)
        formatted_stats = {
            'business_records': raw_stats['business_registrations']['count'],
            'open_businesses': status_counts['open'],
            'closed_businesses': status_counts['closed'],
            'tax_records': raw_stats['commercial_tax']['count'],
            'zoning_polygons': raw_stats.get('zoning_polygons', {}).get('count', 0),
            'businesses_with_zoning': raw_stats.get('businesses_with_zoning', 0),
            'last_updated': raw_stats['business_registrations']['last_updated']
        }
        
        return JSONResponse({
            "status": "success",
            "stats": formatted_stats
        })
        
    except Exception as e:
        logger.error(f"Error getting cache stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting cache stats: {str(e)}")

@router.get("/api/vacancy/filters")
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
        
        # Get unique zoning districts from cache table
        zoning_districts = []
        try:
            from ai.tools.db_utils import execute_with_connection
            
            def get_zoning_districts(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT zoning_district
                    FROM business_registrations_cache
                    WHERE zoning_district IS NOT NULL
                    AND zoning_district != ''
                    ORDER BY zoning_district
                """)
                return [row[0] for row in cursor.fetchall()]
            
            db_result = execute_with_connection(get_zoning_districts)
            if db_result["status"] == "success":
                zoning_districts = db_result["result"]
        except Exception as e:
            logger.warning(f"Could not fetch zoning districts from cache: {e}")
        
        return JSONResponse({
            "status": "success",
            "filters": {
                "industry_types": industry_types,
                "license_types": license_types,
                "business_corridors": business_corridors,
                "districts": districts,
                "zoning_districts": zoning_districts
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_filter_options: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting filter options: {str(e)}")

@router.get("/api/vacancy/zoning-polygons")
async def get_zoning_polygons():
    """
    Fetch zoning district polygons from the database cache for choropleth overlay.
    
    Returns:
        JSON response containing GeoJSON FeatureCollection of zoning district polygons
    """
    try:
        from ai.tools.db_utils import execute_with_connection
        import json
        
        logger.info("Fetching zoning district polygons from cache...")
        
        def get_zoning_polygons_from_db(conn):
            cursor = conn.cursor()
            try:
                # Check if PostGIS geometry column exists
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'zoning_polygons_cache' 
                    AND column_name = 'geometry'
                """)
                has_geometry = cursor.fetchone() is not None
                
                if has_geometry:
                    # Use PostGIS to convert geometry to GeoJSON
                    cursor.execute("""
                        SELECT 
                            zoning,
                            ST_AsGeoJSON(geometry)::json as geometry_json,
                            raw_data
                        FROM zoning_polygons_cache
                        WHERE geometry IS NOT NULL
                        AND zoning IS NOT NULL
                        AND zoning != ''
                        ORDER BY zoning
                    """)
                else:
                    # Fall back to geometry_data column (JSON stored as text)
                    cursor.execute("""
                        SELECT 
                            zoning,
                            geometry_data,
                            raw_data
                        FROM zoning_polygons_cache
                        WHERE geometry_data IS NOT NULL
                        AND zoning IS NOT NULL
                        AND zoning != ''
                        ORDER BY zoning
                    """)
                
                features = []
                for row in cursor.fetchall():
                    zoning = row[0]
                    if has_geometry:
                        geometry_json = row[1]
                    else:
                        # Parse geometry_data if it's a string
                        geometry_data = row[1]
                        if isinstance(geometry_data, str):
                            geometry_json = json.loads(geometry_data)
                        else:
                            geometry_json = geometry_data
                    
                    # Create GeoJSON feature
                    feature = {
                        "type": "Feature",
                        "properties": {
                            "zoning": zoning,
                            "name": zoning
                        },
                        "geometry": geometry_json
                    }
                    features.append(feature)
                
                cursor.close()
                return {
                    "type": "FeatureCollection",
                    "features": features
                }
            except Exception as e:
                cursor.close()
                raise e
        
        db_result = execute_with_connection(get_zoning_polygons_from_db)
        
        if db_result["status"] == "success":
            geojson_data = db_result["result"]
            return JSONResponse({
                "status": "success",
                "count": len(geojson_data.get("features", [])),
                "geojson": geojson_data
            })
        else:
            logger.error(f"Error fetching zoning polygons: {db_result.get('error')}")
            raise HTTPException(
                status_code=500, 
                detail=f"Error fetching zoning polygons: {db_result.get('error')}"
            )
            
    except Exception as e:
        logger.error(f"Error in get_zoning_polygons: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting zoning polygons: {str(e)}")

@router.get("/api/vacancy/tax-filings")
async def get_commercial_tax_filings():
    """Fetch commercial vacancy tax filing dataset"""
    try:
        from ai.tools.data_fetcher import fetch_data_from_api
        
        logger.info("Fetching commercial vacancy tax filing data...")
        
        # Query to get all commercial tax filing data with BAN field
        query = """
        SELECT 
            ban,
            parcelnumber,
            parcelsitusaddress,
            taxyear,
            filed,
            vacant,
            rate,
            filertype,
            entity,
            lin,
            linaddress,
            longitude,
            latitude,
            analysis_neighborhood,
            supervisor_district
        WHERE ban IS NOT NULL AND ban != ''
        """
        
        result = fetch_data_from_api({
            'endpoint': 'rzkk-54yv',
            'query': query
        })
        
        if not result or 'data' not in result:
            logger.error(f"Failed to fetch tax filing data: {result.get('error', 'Unknown error')}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch tax filing data: {result.get('error', 'Unknown error')}")
        
        data = result['data']
        logger.info(f"Successfully fetched {len(data)} commercial tax filing records with BAN field")
        
        return JSONResponse({
            "data": data,
            "total_records": len(data),
            "query_url": result.get('queryURL'),
            "message": f"Successfully fetched {len(data)} commercial tax filing records with BAN field"
        })
        
    except Exception as e:
        logger.exception(f"Error fetching commercial tax filing data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching commercial tax filing data: {str(e)}")

@router.get("/api/vacancy/match-ban")
async def match_ban_data(
    industry_type: Optional[List[str]] = Query(None),
    license_type: Optional[List[str]] = Query(None),
    business_corridor: Optional[List[str]] = Query(None),
    corridor_only: bool = False,
    district: Optional[List[str]] = Query(None),
    matched_ban_only: bool = False,
    limit: Optional[int] = Query(None)
):
    """Get vacancy data with BAN matching from commercial tax filings"""
    try:
        from ai.tools.data_fetcher import fetch_data_from_api
        
        logger.info("Matching BAN data between business registrations and commercial tax filings...")
        
        # First, get the business data (same as regular vacancy endpoint)
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
            full_business_address,
            certificate_number
        WHERE location IS NOT NULL
        """
        
        # Skip filters for now - just test basic functionality
        # TODO: Add proper filter handling for FastAPI Query objects
        
        if limit:
            base_query += f" LIMIT {limit}"
        
        # Fetch business data
        business_result = fetch_data_from_api({
            'endpoint': 'g8m3-pdis',
            'query': base_query
        })
        
        if not business_result or 'data' not in business_result:
            logger.error(f"Failed to fetch business data: {business_result.get('error', 'Unknown error')}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch business data: {business_result.get('error', 'Unknown error')}")
        
        business_data = business_result['data']
        logger.info(f"Fetched {len(business_data)} business records")
        
        # Now fetch commercial tax filing data with BAN field
        tax_query = """
        SELECT 
            ban,
            parcelnumber,
            parcelsitusaddress,
            taxyear,
            filed,
            vacant,
            rate,
            filertype,
            entity,
            lin,
            linaddress,
            longitude,
            latitude,
            analysis_neighborhood,
            supervisor_district
        WHERE ban IS NOT NULL AND ban != ''
        """
        
        tax_result = fetch_data_from_api({
            'endpoint': 'rzkk-54yv',
            'query': tax_query
        })
        
        if not tax_result or 'data' not in tax_result:
            logger.error(f"Failed to fetch tax filing data: {tax_result.get('error', 'Unknown error')}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch tax filing data: {tax_result.get('error', 'Unknown error')}")
        
        tax_data = tax_result['data']
        logger.info(f"Fetched {len(tax_data)} tax filing records with BAN field")
        
        # Create BAN lookup dictionary for efficient matching
        ban_lookup = {}
        for tax_record in tax_data:
            ban = tax_record.get('ban')
            if ban:
                ban_lookup[ban] = tax_record
        
        # Process business data and add BAN matching
        processed_data = []
        matched_count = 0
        
        for business in business_data:
            certificate_number = business.get('certificate_number', '')
            
            # Check if this business has a matching BAN in tax filings
            tax_match = ban_lookup.get(certificate_number)
            has_ban_match = tax_match is not None
            
            if has_ban_match:
                matched_count += 1
            
            # If matched_ban_only filter is enabled, skip businesses without BAN match
            if matched_ban_only and not has_ban_match:
                continue
            
            # Parse coordinates
            location = business.get('location')
            coordinates = [0, 0]  # Default coordinates
            if location and isinstance(location, dict):
                if 'coordinates' in location and isinstance(location['coordinates'], list) and len(location['coordinates']) >= 2:
                    coordinates = [location['coordinates'][1], location['coordinates'][0]]  # [lat, lon]
                elif 'latitude' in location and 'longitude' in location:
                    coordinates = [location['latitude'], location['longitude']]
            
            # Determine business status
            dba_start = business.get('dba_start_date')
            location_start = business.get('location_start_date')
            dba_end = business.get('dba_end_date')
            location_end = business.get('location_end_date')
            admin_closed = business.get('administratively_closed', False)
            
            # Find most recent open and close dates
            open_dates = [d for d in [dba_start, location_start] if d]
            close_dates = [d for d in [dba_end, location_end] if d]
            
            most_recent_open = max(open_dates) if open_dates else None
            most_recent_close = max(close_dates) if close_dates else None
            
            # Determine status
            if admin_closed:
                status = 'Closed'
            elif most_recent_close and (not most_recent_open or most_recent_close > most_recent_open):
                status = 'Closed'
            else:
                status = 'Open'
            
            # Create building-level record compatible with the original vacancy analysis
            processed_record = {
                'location': {
                    'type': 'Point',
                    'coordinates': coordinates
                },
                'dba_name': business.get('dba_name', ''),
                'naic_code_description': business.get('naic_code_description'),
                'lic_code_description': business.get('lic_code_description'),
                'business_corridor': business.get('business_corridor'),
                'supervisor_district': business.get('supervisor_district'),
                'status': status,
                'full_business_address': business.get('full_business_address'),
                'distinct_address': business.get('full_business_address'),  # Use full address as distinct
                'building_address': business.get('full_business_address'),  # Use full address as building
                'business_count': 1,  # Single business per record
                'streetfront_open_count': 1 if status == 'Open' else 0,
                'streetfront_closed_count': 1 if status == 'Closed' else 0,
                'total_streetfront_addresses': 1,
                'upper_floor_open_count': 0,
                'upper_floor_closed_count': 0,
                'total_upper_floor_addresses': 0,
                'addresses_data': [{
                    'address': business.get('full_business_address'),
                    'status': status,
                    'is_street_level': True,  # Assume all are street level for simplicity
                    'business_count': 1,
                    'businesses': [business],
                    'business_names': [business.get('dba_name', '')],
                    'currently_open_businesses': 1 if status == 'Open' else 0,
                    'currently_closed_businesses': 1 if status == 'Closed' else 0
                }],
                'individual_addresses_count': 1,
                # BAN matching specific fields
                'certificate_number': certificate_number,
                'has_ban_match': has_ban_match,
                'ban_data': tax_match if has_ban_match else None
            }
            
            processed_data.append(processed_record)
        
        # Calculate summary statistics
        total_businesses = len(processed_data)
        matched_businesses = matched_count
        match_rate = (matched_businesses / len(business_data) * 100) if business_data else 0
        
        # Calculate district stats
        district_stats = {}
        for record in processed_data:
            district = record.get('supervisor_district', 'Unknown')
            if district not in district_stats:
                district_stats[district] = {
                    'total': 0,
                    'open': 0,
                    'closed': 0,
                    'matched': 0,
                    'vacancy_rate': 0
                }
            
            district_stats[district]['total'] += 1
            if record['status'] == 'Open':
                district_stats[district]['open'] += 1
            else:
                district_stats[district]['closed'] += 1
            
            if record['has_ban_match']:
                district_stats[district]['matched'] += 1
        
        # Calculate vacancy rates for each district
        for district, stats in district_stats.items():
            total = stats['total']
            closed = stats['closed']
            stats['vacancy_rate'] = (closed / total * 100) if total > 0 else 0
        
        summary_stats = {
            'total_businesses': total_businesses,
            'matched_businesses': matched_businesses,
            'match_rate': match_rate,
            'total_tax_records': len(tax_data)
        }
        
        logger.info(f"BAN matching complete: {matched_businesses}/{len(business_data)} businesses matched ({match_rate:.1f}%)")
        
        return JSONResponse({
            "data": processed_data,
            "summary": summary_stats,
            "district_stats": district_stats,
            "query_url": business_result.get('queryURL'),
            "message": f"Successfully matched {matched_businesses} businesses with BAN data ({match_rate:.1f}% match rate)"
        })
        
    except Exception as e:
        logger.exception(f"Error in BAN matching: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error in BAN matching: {str(e)}")


@router.get("/api/vacancy/export-csv")
async def export_vacancy_csv(
    district: Optional[str] = Query(None, description="Supervisor district number (0-11)"),
    zoning_district: Optional[str] = Query(None, description="Zoning district name"),
    street_level_only: bool = Query(True, description="Export only street-level units")
):
    """
    Export vacancy data as CSV filtered by district or zoning district.
    Returns ground-level units with address, status, and active businesses.
    """
    try:
        # Get the cached vacancy data with appropriate filters
        district_list = [district] if district else None
        zoning_list = [zoning_district] if zoning_district else None
        
        # Call the cached endpoint with filters (reuse the logic)
        # We need to get the data directly from the cache
        from ai.tools.simple_business_cache import SimpleBusinessCache
        from ai.tools.db_utils import execute_with_connection
        from collections import defaultdict
        from decimal import Decimal
        from datetime import datetime, date
        import re
        
        cache = SimpleBusinessCache()
        
        # Build query to get cached data
        def get_cached_businesses(conn):
            cursor = conn.cursor()
            
            # Base query
            query = """
                SELECT 
                    location_lat, location_lon,
                    dba_name, certificate_number,
                    naic_code_description, lic_code_description,
                    business_corridor, supervisor_district, zoning_district,
                    dba_start_date, dba_end_date,
                    location_start_date, location_end_date,
                    administratively_closed,
                    full_business_address,
                    normalized_address,
                    is_street_level
                FROM business_registrations_cache
                WHERE location_lat IS NOT NULL AND location_lon IS NOT NULL
            """
            
            params = []
            
            # Add filters
            if district_list:
                placeholders = ','.join(['%s'] * len(district_list))
                query += f" AND supervisor_district IN ({placeholders})"
                params.extend([int(d) if d.isdigit() else d for d in district_list])
            
            if zoning_list:
                placeholders = ','.join(['%s'] * len(zoning_list))
                query += f" AND zoning_district IN ({placeholders})"
                params.extend(zoning_list)
            
            # Apply street_level_only filter at SQL level for consistency with page display
            if street_level_only:
                query += " AND is_street_level = true"
            
            query += " LIMIT 250000"
            
            cursor.execute(query, params)
            
            # Get column names
            if not cursor.description:
                cursor.close()
                return []
            
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            cursor.close()
            
            # Convert rows to dictionaries
            result = []
            for row in rows:
                if isinstance(row, dict):
                    # Already a dict (RealDictCursor)
                    result.append(row)
                else:
                    # Tuple/list - convert to dict
                    try:
                        result.append(dict(zip(columns, row)))
                    except Exception as e:
                        logger.error(f"Error converting row to dict: {e}, row type: {type(row)}, row: {row}")
                        continue
            
            return result
        
        result = execute_with_connection(get_cached_businesses)
        
        # Check if operation was successful
        if result.get('status') != 'success':
            logger.error(f"Failed to get cached businesses: {result.get('message', 'Unknown error')}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch data: {result.get('message', 'Unknown error')}")
        
        raw_data = result.get('result', [])
        
        if not raw_data:
            logger.warning("No data returned from database query")
            raw_data = []
        
        # Validate that all records are dictionaries
        if raw_data:
            # Check first record to ensure it's a dict
            if not isinstance(raw_data[0], dict):
                logger.error(f"Expected dict records, got {type(raw_data[0])}. First record: {raw_data[0]}")
                raise HTTPException(status_code=500, detail="Data format error: records are not dictionaries")
        
        # Process data similar to get_cached_vacancy_data
        # Helper function to determine business status
        def determine_status(record):
            """Determine if a business is Open or Closed based on dates"""
            dba_start_date = record.get('dba_start_date')
            dba_end_date = record.get('dba_end_date')
            location_start_date = record.get('location_start_date')
            location_end_date = record.get('location_end_date')
            administratively_closed = record.get('administratively_closed', False)
            
            if administratively_closed:
                return 'Closed'
            
            if dba_start_date and dba_end_date and dba_start_date == dba_end_date:
                return 'Closed'
            
            if location_start_date and location_end_date and location_start_date == location_end_date:
                return 'Closed'
            
            all_start_dates = [d for d in [dba_start_date, location_start_date] if d is not None]
            all_end_dates = [d for d in [dba_end_date, location_end_date] if d is not None]
            
            most_recent_start = max(all_start_dates) if all_start_dates else None
            most_recent_end = max(all_end_dates) if all_end_dates else None
            
            if most_recent_end:
                if most_recent_start is None or most_recent_end >= most_recent_start:
                    return 'Closed'
                else:
                    return 'Open'
            
            if most_recent_start:
                return 'Open'
            
            return 'Open'
        
        # Group by building address (use normalized_address from DB if available)
        def canonicalize_unit_address(full_address):
            """
            Canonicalize unit addresses to handle variations like:
            - '2139 Polk St A' and '2139 A Polk St' -> '2139 POLK ST A'
            - '2139 Polk St #C' -> '2139 POLK ST C'
            """
            import re
            if not full_address:
                return ''
            
            addr = full_address.strip().upper()
            
            # Extract street number at the start
            match = re.match(r'^(\d+(?:-\d+)?)', addr)
            if not match:
                return addr
            
            street_number = match.group(1)
            rest = addr[len(street_number):].strip()
            
            # Try to extract unit identifier
            unit_letters = []
            
            # Pattern 1: Unit letter at beginning
            unit_match = re.match(r'^([A-Z]|#[A-Z0-9]+)\s+(.+)$', rest)
            if unit_match:
                unit = unit_match.group(1).replace('#', '').strip()
                rest = unit_match.group(2)
                unit_letters.append(unit)
            
            # Pattern 2: Unit letter at end
            unit_match = re.search(r'\s+([A-Z]|#[A-Z0-9]+)$', rest)
            if unit_match:
                unit = unit_match.group(1).replace('#', '').strip()
                rest = rest[:unit_match.start()]
                unit_letters.append(unit)
            
            street_name = rest.strip()
            street_name = re.sub(r'\s+', ' ', street_name)
            
            if unit_letters:
                canonical = f"{street_number} {street_name} {unit_letters[-1]}"
            else:
                canonical = f"{street_number} {street_name}"
            
            return canonical.strip()
        
        building_groups = defaultdict(list)
        for record in raw_data:
            # Ensure record is a dictionary
            if not isinstance(record, dict):
                logger.error(f"Record is not a dict: {type(record)}, value: {record}")
                continue
            
            # Use normalized_address from DB if available, otherwise canonicalize
            normalized = record.get('normalized_address', '')
            if not normalized:
                address = record.get('full_business_address', '')
                normalized = canonicalize_unit_address(address)
            building_groups[normalized].append(record)
        
        # Process buildings
        processed_data = []
        for normalized_address, business_records in building_groups.items():
            # Group by distinct addresses within building
            individual_address_groups = defaultdict(list)
            for record in business_records:
                canonical_address = canonicalize_unit_address(record.get('full_business_address', ''))
                individual_address_groups[canonical_address].append(record)
            
            # Process each distinct address
            addresses_data_formatted = []
            for distinct_address, address_businesses in individual_address_groups.items():
                if not address_businesses:
                    continue
                
                # Check if ANY business is open
                address_has_open_business = False
                address_has_closed_business = False
                
                for business in address_businesses:
                    business_status = determine_status(business)
                    if business_status == 'Open':
                        address_has_open_business = True
                    else:
                        address_has_closed_business = True
                
                # Determine address status
                if address_has_open_business:
                    unit_status = 'Open'
                elif address_has_closed_business:
                    unit_status = 'Closed'
                else:
                    unit_status = 'Unknown'
                
                # Get street level status
                most_recent_business = max(address_businesses,
                    key=lambda r: max(
                        r.get('dba_start_date') or datetime.min,
                        r.get('location_start_date') or datetime.min
                    ))
                street_level = most_recent_business.get('is_street_level', True)
                
                # Convert businesses to serializable format
                businesses_serializable = []
                for biz in address_businesses:
                    biz_data = {
                        'dba_name': biz.get('dba_name', 'Unknown Business'),
                        'certificate_number': biz.get('certificate_number'),
                        'naic_code_description': biz.get('naic_code_description'),
                        'lic_code_description': biz.get('lic_code_description'),
                        'status': determine_status(biz),
                        'dba_start_date': biz.get('dba_start_date'),
                        'location_start_date': biz.get('location_start_date'),
                        'dba_end_date': biz.get('dba_end_date'),
                        'location_end_date': biz.get('location_end_date')
                    }
                    businesses_serializable.append(biz_data)
                
                formatted_unit = {
                    'address': distinct_address,
                    'status': unit_status,
                    'is_street_level': street_level,
                    'businesses': businesses_serializable
                }
                addresses_data_formatted.append(formatted_unit)
            
            # Get first record for building-level data
            first_record = business_records[0]
            
            building_record = {
                'full_business_address': normalized_address,
                'supervisor_district': first_record.get('supervisor_district'),
                'zoning_district': first_record.get('zoning_district'),
                'business_corridor': first_record.get('business_corridor'),
                'addresses_data': addresses_data_formatted
            }
            processed_data.append(building_record)
        
        # Helper function to determine even/odd side of street
        def get_street_side(street_number_str):
            """
            Determine if street number is even (one side) or odd (other side).
            Returns 'Even', 'Odd', or 'Unknown' if can't determine.
            """
            if not street_number_str:
                return 'Unknown'
            
            # Extract first number from range (e.g., "123-125" -> "123")
            first_num_str = street_number_str.split('-')[0].strip()
            
            try:
                first_num = int(first_num_str)
                return 'Even' if first_num % 2 == 0 else 'Odd'
            except (ValueError, AttributeError):
                return 'Unknown'
        
        # Create CSV content with parsed address components
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header with feedback columns first, then parsed address components
        writer.writerow([
            'Correct/Incorrect',  # 1 = correct, 0 = incorrect (for QA feedback)
            'Reason Code',  # Code for explanation (e.g., "can't find address", "business incorrect")
            'Even/Odd',  # Side of street (Even or Odd)
            'Street Number',
            'Street Name',
            'Unit',
            'Canonical Address',
            'Status',
            'Ground Level',
            'Occupied Business Name(s)',
            'Most Recent Closed Business',
            'Close Date',
            'Certificate Numbers',
            'Industry Type',
            'License Type',
            'Corridor',
            'Supervisor District',
            'Zoning District'
        ])
        
        # Collect all rows for sorting
        csv_rows = []
        
        # Extract ground-level units from buildings
        for building in processed_data:
            addresses_data = building.get('addresses_data', [])
            
            # Handle string JSON if needed
            if isinstance(addresses_data, str):
                try:
                    addresses_data = json.loads(addresses_data)
                except:
                    addresses_data = []
            
            if not isinstance(addresses_data, list):
                continue
            
            # Filter to street-level units only
            for addr in addresses_data:
                # Use database value but validate with runtime check (same as cached endpoint)
                db_street_level = addr.get('is_street_level') or addr.get('street_level', False)
                address_string = addr.get('address') or addr.get('distinct_address') or building.get('full_business_address', '')
                
                # Validate with runtime check using updated logic
                # Import the is_upper_floor_address logic
                def is_upper_floor_address_check(full_address: str) -> bool:
                    """Check if address is upper floor using updated logic"""
                    if not full_address:
                        return False
                    import re
                    s = full_address.upper()
                    
                    # Indicators of units/floors
                    if re.search(r'#\s*[0-9]+', s):
                        return True
                    if re.search(r'\b(APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM)\b\s*[A-Z0-9]*', s):
                        return True
                    if re.search(r'\b(FL|FLOOR)\b\s*[0-9A-Z]*', s):
                        return True
                    if re.search(r'\b(2ND|3RD|4TH|5TH|6TH|7TH|8TH|9TH|10TH|11TH|12TH)\b', s):
                        return True
                    
                    # Parse address to extract unit component
                    street_number, street_name, unit = parse_address_components(full_address)
                    
                    # If unit exists and contains any digits, it's upper floor
                    if unit:
                        if re.search(r'\d', unit):
                            return True
                        if re.search(r'[A-Z]\d|\d[A-Z]', unit):
                            return True
                    
                    # Trailing numeric token - all numeric units are now considered upper floor
                    if re.search(r'\s([0-9]+)\s*$', s):
                        return True
                    
                    if re.search(r'\s[0-9]+[A-Z]\s*$', s):
                        return True
                    
                    if re.search(r'\s\d+[-_]\d+', s):
                        return True
                    
                    return False
                
                # Validate street level (inverse of upper floor)
                validated_street_level = not is_upper_floor_address_check(address_string)
                
                # Use validated value (override database if different)
                is_street_level = validated_street_level
                
                if street_level_only and not is_street_level:
                    continue
                
                # Get address
                address = addr.get('address') or addr.get('distinct_address') or building.get('full_business_address', '')
                
                # Get status
                status = addr.get('status') or addr.get('unit_status', 'Unknown')
                
                # Get all businesses at this address
                businesses = addr.get('businesses', [])
                
                # Separate open and closed businesses
                open_businesses = [b for b in businesses if b.get('status') == 'Open']
                closed_businesses = [b for b in businesses if b.get('status') == 'Closed']
                
                # Format occupied business names (most recent open business if multiple)
                occupied_business_names = ''
                if open_businesses:
                    if len(open_businesses) == 1:
                        # Single open business
                        occupied_business_names = open_businesses[0].get('dba_name', 'Unknown')
                    else:
                        # Multiple open businesses - find the one with most recent start date
                        def get_most_recent_start_date(biz):
                            """Get the most recent start date from a business record"""
                            dba_start = biz.get('dba_start_date')
                            location_start = biz.get('location_start_date')
                            if dba_start and location_start:
                                return max(dba_start, location_start)
                            return dba_start or location_start or datetime.min
                        
                        most_recent_open = max(open_businesses, key=get_most_recent_start_date)
                        occupied_business_names = most_recent_open.get('dba_name', 'Unknown')
                
                # Get most recent closed business and close date
                most_recent_closed_business = ''
                close_date = ''
                
                if closed_businesses:
                    # Find the business with the most recent end date
                    def get_most_recent_end_date(biz):
                        """Get the most recent end date from a business record"""
                        dba_end = biz.get('dba_end_date')
                        location_end = biz.get('location_end_date')
                        if dba_end and location_end:
                            return max(dba_end, location_end)
                        return dba_end or location_end or datetime.min
                    
                    most_recent_closed = max(closed_businesses, key=get_most_recent_end_date)
                    most_recent_closed_business = most_recent_closed.get('dba_name', 'Unknown')
                    
                    # Get the most recent end date
                    dba_end = most_recent_closed.get('dba_end_date')
                    location_end = most_recent_closed.get('location_end_date')
                    if dba_end and location_end:
                        close_date = max(dba_end, location_end)
                    else:
                        close_date = dba_end or location_end
                    
                    # Format date as string if it's a date object
                    if close_date and isinstance(close_date, (datetime, date)):
                        close_date = close_date.strftime('%Y-%m-%d')
                    elif close_date:
                        close_date = str(close_date)
                
                # Get certificate numbers from all businesses (open and closed)
                all_certificate_numbers = []
                for b in businesses:
                    cert_num = b.get('certificate_number')
                    if cert_num:
                        all_certificate_numbers.append(str(cert_num))
                certificate_numbers = '; '.join(all_certificate_numbers) if all_certificate_numbers else ''
                
                # Get industry and license types from all businesses (prioritize open)
                all_industry_types = set()
                all_license_types = set()
                for b in businesses:
                    naic = b.get('naic_code_description')
                    lic = b.get('lic_code_description')
                    if naic:
                        all_industry_types.add(naic)
                    if lic:
                        all_license_types.add(lic)
                
                industry_types = '; '.join(sorted(all_industry_types)) if all_industry_types else ''
                license_types = '; '.join(sorted(all_license_types)) if all_license_types else ''
                
                # Parse address into components for sorting
                street_number, street_name, unit = parse_address_components(address)
                
                # Determine even/odd side of street
                street_side = get_street_side(street_number)
                
                # Convert street number to integer for numeric sorting (handle ranges like "123-125")
                street_number_for_sort = street_number.split('-')[0] if street_number else ""
                try:
                    street_number_int = int(street_number_for_sort) if street_number_for_sort.isdigit() else 0
                except (ValueError, AttributeError):
                    street_number_int = 0
                
                # Convert unit to sortable format (letters first, then numbers)
                # For sorting: empty < letters < numbers
                if not unit:
                    unit_sort_key = (0, "")  # Empty units sort first
                elif unit.isalpha():
                    unit_sort_key = (1, unit)  # Letters sort after empty, before numbers
                else:
                    # Extract numeric part for sorting
                    unit_num_match = re.search(r'\d+', unit)
                    unit_num = int(unit_num_match.group(0)) if unit_num_match else 999999
                    unit_sort_key = (2, unit_num, unit)  # Numbers sort last
                
                # Store row data for sorting
                csv_rows.append({
                    'correct_incorrect': '',  # Empty for user to fill (1 = correct, 0 = incorrect)
                    'reason_code': '',  # Empty for user to fill (e.g., "can't find address", "business incorrect")
                    'even_odd': street_side,  # Calculated: Even, Odd, or Unknown
                    'street_number': street_number,
                    'street_name': street_name,
                    'unit': unit,
                    'canonical_address': address,
                    'status': status,
                    'is_street_level': is_street_level,
                    'occupied_business_names': occupied_business_names or 'None',
                    'most_recent_closed_business': most_recent_closed_business or 'N/A',
                    'close_date': close_date or 'N/A',
                    'certificate_numbers': certificate_numbers or 'None',
                    'industry_types': industry_types or 'None',
                    'license_types': license_types or 'None',
                    'corridor': building.get('business_corridor') or 'None',
                    'supervisor_district': building.get('supervisor_district') or 'Unknown',
                    'zoning_district': building.get('zoning_district') or 'Unknown',
                    # Sort keys
                    'sort_street_name': street_name,
                    'sort_street_number': street_number_int,
                    'sort_unit': unit_sort_key
                })
        
        # Sort rows: street name (alphabetical), then street number (numeric), then unit (empty < letters < numbers)
        csv_rows.sort(key=lambda x: (
            x['sort_street_name'],
            x['sort_street_number'],
            x['sort_unit']
        ))
        
        # Write sorted rows
        for row in csv_rows:
            writer.writerow([
                row['correct_incorrect'],  # Empty for user to fill (1 = correct, 0 = incorrect)
                row['reason_code'],  # Empty for user to fill
                row['even_odd'],  # Even, Odd, or Unknown
                row['street_number'],
                row['street_name'],
                row['unit'],
                row['canonical_address'],
                row['status'],
                'Yes' if row['is_street_level'] else 'No',
                row['occupied_business_names'],
                row['most_recent_closed_business'],
                row['close_date'],
                row['certificate_numbers'],
                row['industry_types'],
                row['license_types'],
                row['corridor'],
                row['supervisor_district'],
                row['zoning_district']
            ])
        
        # Prepare filename
        if district:
            filename = f"vacancy_district_{district}_ground_level.csv"
        elif zoning_district:
            safe_zoning = zoning_district.replace(' ', '_').replace('/', '_')
            filename = f"vacancy_zoning_{safe_zoning}_ground_level.csv"
        else:
            filename = "vacancy_all_ground_level.csv"
        
        # Create response
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
        
    except Exception as e:
        logger.exception(f"Error exporting vacancy CSV: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error exporting CSV: {str(e)}")