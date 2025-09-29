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
from typing import Dict, Any, Optional, List
import json
from background_jobs import job_manager

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
            """Return True if the address indicates an upper-floor/unit (non-storefront)."""
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
            # Trailing numeric token (e.g., "945 TARAVAL ST 1045")
            # But exclude 100s addresses which are typically ground floor (e.g., "2001 UNION ST 107")
            trailing_number_match = re.search(r'\s([0-9]+)\s*$', s)
            if trailing_number_match:
                trailing_number = int(trailing_number_match.group(1))
                # Only consider it upper floor if it's 200 or higher (100s are typically ground floor)
                if trailing_number >= 200:
                    return True
            # Addresses ending with letter (e.g., "101 LOMBARD ST 23W", "101 LOMBARD ST 409W")
            if re.search(r'\s[0-9]+[A-Z]\s*$', s):
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

                # Sort by start date to get the most recent business
                sorted_businesses = sorted(address_businesses,
                                         key=lambda b: parse_date(b.get('dba_start_date')) or parse_date(b.get('location_start_date')) or datetime.min,
                                         reverse=True)

                most_recent_business = sorted_businesses[0] if sorted_businesses else None
                if not most_recent_business:
                    continue

                # Determine if this is a street-level address using robust detection
                is_street_level = not is_upper_floor_address(individual_address)

                # Get status of most recent business
                dba_start = parse_date(most_recent_business.get('dba_start_date'))
                location_start = parse_date(most_recent_business.get('location_start_date'))
                dba_end = parse_date(most_recent_business.get('dba_end_date'))
                location_end = parse_date(most_recent_business.get('location_end_date'))

                all_open_dates = []
                all_close_dates = []
                if dba_start:
                    all_open_dates.append(dba_start)
                if location_start:
                    all_open_dates.append(location_start)
                if dba_end:
                    all_close_dates.append(dba_end)
                if location_end:
                    all_close_dates.append(location_end)

                most_recent_open_date = max(all_open_dates) if all_open_dates else None
                most_recent_close_date = max(all_close_dates) if all_close_dates else None

                # Determine individual address status
                if not most_recent_close_date:
                    individual_status = 'Open'
                elif not most_recent_open_date:
                    individual_status = 'Closed'
                elif most_recent_close_date > most_recent_open_date:
                    individual_status = 'Closed'
                else:
                    individual_status = 'Open'

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
                "total_businesses": sum(d['business_count'] for d in processed_data)
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
    status: Optional[str] = Query(None),
    commercial_tax_filing_only: bool = Query(False, description="Filter to only buildings with commercial tax filings"),
    limit: int = Query(50000, description="Maximum number of results to return")
):
    """Get vacancy data from local cache - much faster than API calls"""
    try:
        import json
        from ai.tools.db_utils import execute_with_connection
        
        logger.info(f"Getting cached vacancy data with search: {business_search}")
        
        # Check if cache tables exist first
        def check_cache_exists(conn):
            cursor = conn.cursor()
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'business_cache'
                );
            """)
            exists = cursor.fetchone()[0]
            cursor.close()
            return exists
        
        cache_result = execute_with_connection(check_cache_exists)
        cache_exists = cache_result.get('result', False) if cache_result.get('status') == 'success' else False
        
        if not cache_exists:
            logger.warning("Business cache tables don't exist. Falling back to API endpoint.")
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
                data['cache_note'] = "Using API data. Run 'python setup_business_cache.py --populate' to enable fast cached searches."
                return JSONResponse(data)
            return result
        
        # Build SQL query for cached data
        base_query = f"""
        SELECT 
            id, certificate_number, dba_name, naic_code_description, lic_code_description,
            business_corridor, supervisor_district, full_business_address, distinct_address,
            building_address, location_lat, location_lon, status, is_street_level,
            business_count, streetfront_open_count, streetfront_closed_count,
            total_streetfront_addresses, upper_floor_open_count, upper_floor_closed_count,
            total_upper_floor_addresses, individual_addresses_count, addresses_data,
            has_commercial_tax_filing, tax_filing_addresses, vacancy_status, tax_filing_year,
            tax_filing_ban, tax_filing_entity
        FROM business_cache
        WHERE 1=1
        """
        
        params = []
        param_count = 0
        
        # Add business search filter
        if business_search and business_search.strip():
            param_count += 1
            base_query += f" AND dba_name ILIKE %s"
            params.append(f"%{business_search.strip()}%")
        
        # Add industry type filter
        if industry_type and industry_type:
            if isinstance(industry_type, list):
                industry_conditions = " OR ".join([f"naic_code_description ILIKE %s" for _ in industry_type])
                base_query += f" AND ({industry_conditions})"
                for item in industry_type:
                    params.append(f"%{item}%")
            else:
                param_count += 1
                base_query += f" AND naic_code_description ILIKE %s"
                params.append(f"%{industry_type}%")
        
        # Add license type filter
        if license_type and license_type:
            if isinstance(license_type, list):
                license_conditions = " OR ".join([f"lic_code_description ILIKE %s" for _ in license_type])
                base_query += f" AND ({license_conditions})"
                for item in license_type:
                    params.append(f"%{item}%")
            else:
                param_count += 1
                base_query += f" AND lic_code_description ILIKE %s"
                params.append(f"%{license_type}%")
        
        # Add business corridor filter
        if business_corridor and business_corridor:
            if isinstance(business_corridor, list):
                corridor_conditions = " OR ".join([f"business_corridor ILIKE %s" for _ in business_corridor])
                base_query += f" AND ({corridor_conditions})"
                for item in business_corridor:
                    params.append(f"%{item}%")
            else:
                param_count += 1
                base_query += f" AND business_corridor ILIKE %s"
                params.append(f"%{business_corridor}%")
        
        # Add corridor only filter
        if corridor_only:
            base_query += f" AND business_corridor IS NOT NULL AND business_corridor != ''"
        
        # Add district filter
        if district and district:
            if isinstance(district, list):
                district_conditions = " OR ".join([f"supervisor_district = %s" for _ in district])
                base_query += f" AND ({district_conditions})"
                for item in district:
                    params.append(str(item))
            else:
                param_count += 1
                base_query += f" AND supervisor_district = %s"
                params.append(str(district))
        
        # Add status filter
        if status and status.strip():
            param_count += 1
            base_query += f" AND status = %s"
            params.append(status.strip())
        
        # Add commercial tax filing filter
        if commercial_tax_filing_only:
            base_query += f" AND has_commercial_tax_filing = TRUE"
        
        # Add ordering and limit
        base_query += " ORDER BY dba_name"
        base_query += f" LIMIT {min(limit, 250000)}"
        
        logger.info(f"Executing cached query: {base_query}")
        logger.info(f"With params: {params}")
        
        def get_cached_data(conn):
            cursor = conn.cursor()
            cursor.execute(base_query, params)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            cursor.close()
            return [dict(zip(columns, row)) for row in rows]
        
        cached_result = execute_with_connection(get_cached_data)
        cached_data = cached_result.get('result', []) if cached_result.get('status') == 'success' else []
        
        logger.info(f"Raw cached data count: {len(cached_data)}")
        if cached_data:
            logger.info(f"First record keys: {list(cached_data[0].keys())}")
            logger.info(f"First record dba_name: {cached_data[0].get('dba_name')}")
            logger.info(f"First record addresses_data type: {type(cached_data[0].get('addresses_data'))}")
        
        # Convert to the format expected by the frontend
        processed_data = []
        for record in cached_data:
            # Parse addresses_data - it might be a list or JSON string
            addresses_data = []
            if record.get('addresses_data'):
                if isinstance(record['addresses_data'], list):
                    # Already a list
                    addresses_data = record['addresses_data']
                else:
                    # Try to parse as JSON string
                    try:
                        addresses_data = json.loads(record['addresses_data'])
                    except (json.JSONDecodeError, TypeError):
                        addresses_data = []
            
            # Extract coordinates from addresses_data or use location_lat/lon as fallback
            coordinates = [None, None]
            
            # First try to get coordinates from addresses_data
            if addresses_data and len(addresses_data) > 0:
                first_address = addresses_data[0]
                if 'location' in first_address and 'coordinates' in first_address['location']:
                    coords = first_address['location']['coordinates']
                    if len(coords) >= 2:
                        coordinates = [coords[0], coords[1]]  # [lon, lat]
            
            # If no coordinates found in addresses_data, fall back to location_lat/lon
            if coordinates == [None, None] and record['location_lon'] is not None and record['location_lat'] is not None:
                # Fallback to location_lat/lon fields - now stored as FLOAT
                # Convert Decimal to float if needed
                from decimal import Decimal
                lat = float(record['location_lat']) if isinstance(record['location_lat'], Decimal) else record['location_lat']
                lon = float(record['location_lon']) if isinstance(record['location_lon'], Decimal) else record['location_lon']
                coordinates = [lon, lat]
            
            # Convert numeric fields for JSON serialization (now mostly FLOAT types)
            def convert_numeric(value):
                if value is None:
                    return None
                from decimal import Decimal
                if isinstance(value, Decimal):
                    return float(value)
                return value
            
            # Normalize status field to proper case
            status = record['status']
            if status:
                status = status.lower()
                if status == 'open':
                    status = 'Open'
                elif status == 'closed':
                    status = 'Closed'
                else:
                    status = 'Unknown'
            else:
                status = 'Unknown'
            
            # Process addresses_data to match the expected format from non-cached version
            processed_addresses_data = []
            for addr in addresses_data:
                processed_addr = {
                    'address': addr.get('full_business_address', ''),
                    'distinct_address': addr.get('full_business_address', ''),
                    'status': status,  # Use building-level status
                    'is_street_level': addr.get('is_street_level', True),
                    'business_count': 1,
                    'dba_name': addr.get('dba_name', 'Unknown Business'),
                    'open_date': addr.get('dba_start_date'),
                    'close_date': addr.get('dba_end_date'),
                    'businesses': [addr]
                }
                processed_addresses_data.append(processed_addr)
            
            processed_record = {
                'location': {
                    'type': 'Point',
                    'coordinates': coordinates
                },
                'dba_name': record['dba_name'] or 'Unknown Business',
                'naic_code_description': record['naic_code_description'],
                'lic_code_description': record['lic_code_description'],
                'business_corridor': record['business_corridor'],
                'supervisor_district': record['supervisor_district'],
                'status': status,
                'full_business_address': record['full_business_address'] or 'Unknown Address',
                'distinct_address': record['distinct_address'] or record['full_business_address'] or 'Unknown Address',
                'building_address': record['building_address'] or record['full_business_address'] or 'Unknown Address',
                'business_count': convert_numeric(record['business_count']),
                'streetfront_open_count': convert_numeric(record['streetfront_open_count']),
                'streetfront_closed_count': convert_numeric(record['streetfront_closed_count']),
                'total_streetfront_addresses': convert_numeric(record['total_streetfront_addresses']),
                'upper_floor_open_count': convert_numeric(record['upper_floor_open_count']),
                'upper_floor_closed_count': convert_numeric(record['upper_floor_closed_count']),
                'total_upper_floor_addresses': convert_numeric(record['total_upper_floor_addresses']),
                'addresses_data': processed_addresses_data,
                'individual_addresses_count': convert_numeric(record['individual_addresses_count']),
                'has_commercial_tax_filing': record.get('has_commercial_tax_filing', False),
                'tax_filing_addresses': record.get('tax_filing_addresses', []),
                'vacancy_status': record.get('vacancy_status', 'unfiled'),
                'tax_filing_year': convert_numeric(record.get('tax_filing_year', 0)),
                'tax_filing_ban': record.get('tax_filing_ban', ''),
                'tax_filing_entity': record.get('tax_filing_entity', '')
            }
            processed_data.append(processed_record)
        
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
                "total_businesses": sum(d['business_count'] for d in processed_data)
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
        search_query = f"""
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
        AND dba_name LIKE '%{search_term}%'
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
    limit: Optional[int] = Query(None, description="Limit number of business records to process")
):
    """Refresh the business cache by fetching fresh data from DataSF API (async job)"""
    try:
        import asyncio
        
        logger.info("Starting business cache refresh job...")
        
        # Create a background job
        job_id = job_manager.create_job("business_cache_refresh", f"Refresh business cache (limit: {limit or 'all'})")
        logger.info(f"Created job {job_id} for business cache refresh")
        
        # Debug: List all jobs before verification
        all_jobs_before = job_manager.get_all_jobs()
        logger.info(f"All jobs before verification: {list(all_jobs_before.keys())}")
        
        # Verify job was created
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Failed to create job {job_id}")
            raise HTTPException(status_code=500, detail="Failed to create background job")
        
        logger.info(f"Job {job_id} verified, starting background task")
        
        # Debug: List all jobs after verification
        all_jobs_after = job_manager.get_all_jobs()
        logger.info(f"All jobs after verification: {list(all_jobs_after.keys())}")
        
        # Start the job in the background with a small delay to ensure job is created
        async def delayed_start():
            await asyncio.sleep(0.1)  # Small delay to ensure job is created
            await _run_business_cache_refresh_job(job_id, limit)
        
        task = asyncio.create_task(delayed_start())
        logger.info(f"Created background task for business cache refresh job {job_id}")
        
        return JSONResponse({
            "status": "success",
            "message": "Business cache refresh started",
            "job_id": job_id
        })
        
    except Exception as e:
        logger.error(f"Error starting business cache refresh job: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error starting business cache refresh: {str(e)}")

async def _run_business_cache_refresh_job(job_id: str, limit: Optional[int] = None):
    """Run the business cache refresh job in the background."""
    try:
        from ai.tools.business_cache_processor import BusinessCacheProcessor
        import asyncio
        
        logger.info(f"Starting business cache refresh job {job_id} with limit {limit}")
        
        # Mark job as running
        job = job_manager.get_job(job_id)
        if job:
            job.start()
            logger.info(f"Marked job {job_id} as running")
        
        # Run the cache refresh in a thread pool to avoid blocking
        def run_cache_refresh():
            processor = BusinessCacheProcessor()
            return processor.refresh_cache(limit)
        
        # Execute in thread pool to prevent blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_cache_refresh)
        
        # Add a small delay to ensure database transaction is committed
        await asyncio.sleep(0.5)
        
        # Complete the job with results
        job = job_manager.get_job(job_id)
        if job:
            job.complete({
                "business_records": result['business_records'],
                "tax_records": result['tax_records'],
                "duration_seconds": result['duration_seconds']
            })
            logger.info(f"Business cache refresh job {job_id} completed successfully")
        else:
            logger.error(f"Job {job_id} not found when trying to complete")
        
    except Exception as e:
        logger.error(f"Business cache refresh job {job_id} failed: {str(e)}", exc_info=True)
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
        from ai.tools.business_cache_processor import BusinessCacheProcessor
        
        processor = BusinessCacheProcessor()
        stats = processor.get_cache_stats()
        
        return JSONResponse({
            "status": "success",
            "stats": stats
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
        
        return JSONResponse({
            "status": "success",
            "filters": {
                "industry_types": industry_types,
                "license_types": license_types,
                "business_corridors": business_corridors,
                "districts": districts
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_filter_options: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting filter options: {str(e)}")

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