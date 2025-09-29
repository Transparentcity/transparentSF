"""
SOQL-Based Business Data Processor

This module uses a single SOQL query with GROUP BY and CASE logic to process
business registration data and create location-based records efficiently.

Key Features:
- Single SOQL query with GROUP BY location
- CASE logic to determine open/closed status
- Direct insertion of query results
- Much simpler and faster than complex processing
"""

import logging
import time
import psycopg2
import psycopg2.extras
from typing import Dict, List, Optional, Any
import json
import os
from dotenv import load_dotenv
from ai.tools.data_fetcher import fetch_data_from_api

# Load environment variables
load_dotenv('ai/.env')

logger = logging.getLogger(__name__)

class SOQLBusinessProcessor:
    """Simple processor using SOQL GROUP BY and CASE logic"""
    
    def __init__(self, db_connection_string: str = None):
        """Initialize the processor with database connection"""
        self.db_connection_string = db_connection_string or os.getenv('DATABASE_URL')
        if not self.db_connection_string:
            raise ValueError("Database connection string not provided")
        
        self.batch_size = 1000
        self.total_processed = 0
        self.total_errors = 0
    
    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(self.db_connection_string)
    
    def create_table_if_not_exists(self):
        """Create the location_business table if it doesn't exist"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    create_sql = """
                    CREATE TABLE IF NOT EXISTS location_business (
                        id SERIAL PRIMARY KEY,
                        -- Location identifier
                        location_key TEXT UNIQUE,
                        full_business_address TEXT,
                        latitude FLOAT,
                        longitude FLOAT,
                        
                        -- Business data
                        certificate_number TEXT,
                        dba_name TEXT,
                        city TEXT,
                        state TEXT,
                        business_zip TEXT,
                        
                        -- Status determination
                        location_start_date TIMESTAMP,
                        location_end_date TIMESTAMP,
                        dba_start_date TIMESTAMP,
                        dba_end_date TIMESTAMP,
                        administratively_closed BOOLEAN,
                        is_open BOOLEAN,
                        
                        -- Representative fields
                        naic_code TEXT,
                        naic_code_description TEXT,
                        lic TEXT,
                        lic_code_description TEXT,
                        business_corridor TEXT,
                        supervisor_district TEXT,
                        neighborhoods_analysis_boundaries TEXT,
                        location JSONB,
                        
                        -- Metadata
                        most_recent_date TIMESTAMP,
                        record_count INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    
                    CREATE INDEX IF NOT EXISTS idx_location_business_location_key ON location_business(location_key);
                    CREATE INDEX IF NOT EXISTS idx_location_business_coordinates ON location_business(latitude, longitude);
                    CREATE INDEX IF NOT EXISTS idx_location_business_is_open ON location_business(is_open);
                    CREATE INDEX IF NOT EXISTS idx_location_business_corridor ON location_business(business_corridor);
                    CREATE INDEX IF NOT EXISTS idx_location_business_district ON location_business(supervisor_district);
                    """
                    cur.execute(create_sql)
                    conn.commit()
                    logger.info("Created location_business table with indexes")
                    
        except Exception as e:
            logger.error(f"Error creating table: {e}")
            raise
    
    def clear_existing_data(self):
        """Clear existing location business data"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM location_business")
                    conn.commit()
                    logger.info("Cleared existing location business data")
        except Exception as e:
            logger.error(f"Error clearing data: {e}")
            raise
    
    def build_soql_query(self) -> str:
        """Build a simple SOQL query that works with DataSF's limitations"""
        
        # Simple SOQL query - just get all records and let Python do the grouping
        # SOQL doesn't support CTEs, window functions, or complex CASE statements
        
        query = """
        SELECT 
            certificate_number,
            dba_name,
            full_business_address,
            city,
            state,
            business_zip,
            dba_start_date,
            dba_end_date,
            location_start_date,
            location_end_date,
            administratively_closed,
            naic_code,
            naic_code_description,
            lic,
            lic_code_description,
            business_corridor,
            supervisor_district,
            neighborhoods_analysis_boundaries,
            location
        WHERE certificate_number IS NOT NULL
        AND full_business_address IS NOT NULL
        AND location IS NOT NULL
        AND location.latitude IS NOT NULL
        AND location.longitude IS NOT NULL
        ORDER BY location_start_date DESC
        LIMIT 50000
        """
        
        return query
    
    def fetch_business_data(self) -> List[Dict[str, Any]]:
        """Fetch business data using simple SOQL query"""
        logger.info("Fetching business data with SOQL query")
        
        query = self.build_soql_query()
        
        try:
            result = fetch_data_from_api({
                'endpoint': 'g8m3-pdis',
                'query': query
            })
            
            if not result or 'data' not in result:
                raise Exception(f"Failed to fetch business data: {result.get('error', 'Unknown error')}")
            
            logger.info(f"Fetched {len(result['data'])} business records")
            return result['data']
            
        except Exception as e:
            logger.error(f"Failed to fetch business data: {e}")
            raise
    
    def parse_location_coordinates(self, location_data: Any) -> tuple[float, float]:
        """Parse coordinates from location JSON data"""
        if not location_data:
            return 0.0, 0.0
        
        try:
            if isinstance(location_data, dict):
                # Check for GeoJSON format first (coordinates array)
                if 'coordinates' in location_data and isinstance(location_data['coordinates'], list) and len(location_data['coordinates']) >= 2:
                    # GeoJSON format: [longitude, latitude]
                    lon, lat = location_data['coordinates'][0], location_data['coordinates'][1]
                    return float(lat), float(lon)
                # Check for direct latitude/longitude fields
                elif 'latitude' in location_data and 'longitude' in location_data:
                    lat = location_data.get('latitude', 0.0)
                    lon = location_data.get('longitude', 0.0)
                    return float(lat), float(lon)
                else:
                    return 0.0, 0.0
            elif isinstance(location_data, str):
                # Try to parse as JSON
                loc_dict = json.loads(location_data)
                # Check for GeoJSON format first
                if 'coordinates' in loc_dict and isinstance(loc_dict['coordinates'], list) and len(loc_dict['coordinates']) >= 2:
                    # GeoJSON format: [longitude, latitude]
                    lon, lat = loc_dict['coordinates'][0], loc_dict['coordinates'][1]
                    return float(lat), float(lon)
                # Check for direct latitude/longitude fields
                elif 'latitude' in loc_dict and 'longitude' in loc_dict:
                    lat = loc_dict.get('latitude', 0.0)
                    lon = loc_dict.get('longitude', 0.0)
                    return float(lat), float(lon)
                else:
                    return 0.0, 0.0
            else:
                return 0.0, 0.0
        except (ValueError, TypeError, KeyError, IndexError):
            return 0.0, 0.0
    
    def group_businesses_by_location(self, business_data: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Group business records by location key"""
        logger.info(f"Grouping {len(business_data)} business records by location")
        
        location_groups = {}
        
        for business in business_data:
            try:
                # Parse coordinates
                lat, lon = self.parse_location_coordinates(business.get('location'))
                if lat == 0.0 and lon == 0.0:
                    continue  # Skip records without valid coordinates
                
                address = business.get('full_business_address', '').strip()
                if not address:
                    continue  # Skip records without address
                
                # Create location key (rounded coordinates for grouping)
                lat_rounded = round(lat, 6)
                lon_rounded = round(lon, 6)
                location_key = f"{address}|{lat_rounded}|{lon_rounded}"
                
                if location_key not in location_groups:
                    location_groups[location_key] = []
                
                location_groups[location_key].append(business)
                
            except Exception as e:
                logger.warning(f"Error processing business record: {e}")
                self.total_errors += 1
        
        logger.info(f"Created {len(location_groups)} location groups")
        return location_groups
    
    def determine_business_status(self, record: Dict[str, Any]) -> bool:
        """Determine if a location is currently open or closed"""
        try:
            # Get the most recent dates
            location_start = record.get('location_start_date')
            location_end = record.get('location_end_date')
            administratively_closed = record.get('administratively_closed', False)
            
            # Parse dates if they're strings
            if isinstance(location_start, str):
                location_start = datetime.fromisoformat(location_start.replace('T', ' ').replace('Z', ''))
            if isinstance(location_end, str):
                location_end = datetime.fromisoformat(location_end.replace('T', ' ').replace('Z', ''))
            
            # Location is open if:
            # 1. Has a recent location_start_date without a location_end_date, OR
            # 2. Has a location_start_date that's more recent than location_end_date, OR
            # 3. No location_end_date and not administratively closed
            is_open = False
            
            if location_start:
                if not location_end:
                    # Started but never ended
                    is_open = True
                elif location_start > location_end:
                    # Started after ending (reopened)
                    is_open = True
                else:
                    # Started and ended, check if recently ended
                    is_open = False
            
            # Override with administrative closure
            if administratively_closed:
                is_open = False
            
            return is_open
            
        except Exception as e:
            logger.warning(f"Error determining status for record: {e}")
            return False
    
    def get_most_recent_record(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get the most recent record from a list of records for the same location"""
        if not records:
            return {}
        
        def get_most_recent_date(record):
            location_start = record.get('location_start_date')
            location_end = record.get('location_end_date')
            dba_start = record.get('dba_start_date')
            dba_end = record.get('dba_end_date')
            
            # Find the most recent date among all date fields
            dates = []
            for date_str in [location_start, location_end, dba_start, dba_end]:
                if date_str:
                    try:
                        if isinstance(date_str, str):
                            date_obj = datetime.fromisoformat(date_str.replace('T', ' ').replace('Z', ''))
                        else:
                            date_obj = date_str
                        dates.append(date_obj)
                    except (ValueError, TypeError):
                        continue
            
            return max(dates) if dates else datetime.min
        
        # Sort by most recent date
        return max(records, key=get_most_recent_date)
    
    def process_location_groups(self, location_groups: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Process grouped business records to create location-based records"""
        logger.info(f"Processing {len(location_groups)} location groups")
        
        processed_records = []
        
        for location_key, records in location_groups.items():
            try:
                # Get the most recent record for this location
                most_recent_record = self.get_most_recent_record(records)
                
                # Parse coordinates
                lat, lon = self.parse_location_coordinates(most_recent_record.get('location'))
                
                # Determine status
                is_open = self.determine_business_status(most_recent_record)
                
                # Create processed record
                processed_record = {
                    'location_key': location_key,
                    'latitude': lat,
                    'longitude': lon,
                    'full_business_address': most_recent_record.get('full_business_address', ''),
                    'certificate_number': most_recent_record.get('certificate_number', ''),
                    'dba_name': most_recent_record.get('dba_name', ''),
                    'city': most_recent_record.get('city', ''),
                    'state': most_recent_record.get('state', ''),
                    'business_zip': most_recent_record.get('business_zip', ''),
                    'location_start_date': most_recent_record.get('location_start_date'),
                    'location_end_date': most_recent_record.get('location_end_date'),
                    'dba_start_date': most_recent_record.get('dba_start_date'),
                    'dba_end_date': most_recent_record.get('dba_end_date'),
                    'administratively_closed': most_recent_record.get('administratively_closed', False),
                    'is_open': is_open,
                    'naic_code': most_recent_record.get('naic_code'),
                    'naic_code_description': most_recent_record.get('naic_code_description'),
                    'lic': most_recent_record.get('lic'),
                    'lic_code_description': most_recent_record.get('lic_code_description'),
                    'business_corridor': most_recent_record.get('business_corridor'),
                    'supervisor_district': most_recent_record.get('supervisor_district'),
                    'neighborhoods_analysis_boundaries': most_recent_record.get('neighborhoods_analysis_boundaries'),
                    'location': most_recent_record.get('location'),
                    'record_count': len(records)
                }
                
                processed_records.append(processed_record)
                
            except Exception as e:
                logger.warning(f"Error processing location group {location_key}: {e}")
                self.total_errors += 1
        
        logger.info(f"Processed {len(processed_records)} location records")
        return processed_records
    
    
    def create_location_key(self, address: str, lat: float, lon: float) -> str:
        """Create a unique key for location grouping"""
        return f"{address}|{lat}|{lon}"
    
    def insert_processed_records(self, records: List[Dict[str, Any]]):
        """Insert processed records into the database"""
        if not records:
            return
        
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Prepare insert statement
                    insert_sql = """
                    INSERT INTO location_business (
                        location_key, full_business_address, latitude, longitude,
                        certificate_number, dba_name, city, state, business_zip,
                        location_start_date, location_end_date, dba_start_date, dba_end_date,
                        administratively_closed, is_open,
                        naic_code, naic_code_description, lic, lic_code_description,
                        business_corridor, supervisor_district, neighborhoods_analysis_boundaries,
                        location, most_recent_date, record_count
                    ) VALUES (
                        %(location_key)s, %(full_business_address)s, %(latitude)s, %(longitude)s,
                        %(certificate_number)s, %(dba_name)s, %(city)s, %(state)s, %(business_zip)s,
                        %(location_start_date)s, %(location_end_date)s, %(dba_start_date)s, %(dba_end_date)s,
                        %(administratively_closed)s, %(is_open)s,
                        %(naic_code)s, %(naic_code_description)s, %(lic)s, %(lic_code_description)s,
                        %(business_corridor)s, %(supervisor_district)s, %(neighborhoods_analysis_boundaries)s,
                        %(location)s, %(most_recent_date)s, %(record_count)s
                    ) ON CONFLICT (location_key) DO UPDATE SET
                        full_business_address = EXCLUDED.full_business_address,
                        latitude = EXCLUDED.latitude,
                        longitude = EXCLUDED.longitude,
                        certificate_number = EXCLUDED.certificate_number,
                        dba_name = EXCLUDED.dba_name,
                        city = EXCLUDED.city,
                        state = EXCLUDED.state,
                        business_zip = EXCLUDED.business_zip,
                        location_start_date = EXCLUDED.location_start_date,
                        location_end_date = EXCLUDED.location_end_date,
                        dba_start_date = EXCLUDED.dba_start_date,
                        dba_end_date = EXCLUDED.dba_end_date,
                        administratively_closed = EXCLUDED.administratively_closed,
                        is_open = EXCLUDED.is_open,
                        naic_code = EXCLUDED.naic_code,
                        naic_code_description = EXCLUDED.naic_code_description,
                        lic = EXCLUDED.lic,
                        lic_code_description = EXCLUDED.lic_code_description,
                        business_corridor = EXCLUDED.business_corridor,
                        supervisor_district = EXCLUDED.supervisor_district,
                        neighborhoods_analysis_boundaries = EXCLUDED.neighborhoods_analysis_boundaries,
                        location = EXCLUDED.location,
                        most_recent_date = EXCLUDED.most_recent_date,
                        record_count = EXCLUDED.record_count,
                        updated_at = CURRENT_TIMESTAMP
                    """
                    
                    # Process records for insertion
                    record_dicts = []
                    for record in records:
                        try:
                            # Extract coordinates
                            lat = float(record.get('latitude', 0.0))
                            lon = float(record.get('longitude', 0.0))
                            address = record.get('full_business_address', '')
                            
                            if lat == 0.0 and lon == 0.0:
                                continue  # Skip invalid coordinates
                            
                            # Use location_key from query, or create one if missing
                            location_key = record.get('location_key')
                            if not location_key:
                                location_key = self.create_location_key(address, lat, lon)
                            
                            # Get is_open status from query (already calculated)
                            is_open = record.get('is_open', False)
                            
                            # Prepare record for insertion
                            record_dict = {
                                'location_key': location_key,
                                'full_business_address': address,
                                'latitude': lat,
                                'longitude': lon,
                                'certificate_number': record.get('certificate_number', ''),
                                'dba_name': record.get('dba_name', ''),
                                'city': record.get('city', ''),
                                'state': record.get('state', ''),
                                'business_zip': record.get('business_zip', ''),
                                'location_start_date': record.get('location_start_date'),
                                'location_end_date': record.get('location_end_date'),
                                'dba_start_date': record.get('dba_start_date'),
                                'dba_end_date': record.get('dba_end_date'),
                                'administratively_closed': record.get('administratively_closed', False),
                                'is_open': is_open,
                                'naic_code': record.get('naic_code'),
                                'naic_code_description': record.get('naic_code_description'),
                                'lic': record.get('lic'),
                                'lic_code_description': record.get('lic_code_description'),
                                'business_corridor': record.get('business_corridor'),
                                'supervisor_district': record.get('supervisor_district'),
                                'neighborhoods_analysis_boundaries': record.get('neighborhoods_analysis_boundaries'),
                                'location': json.dumps(record.get('location')) if record.get('location') else None,
                                'most_recent_date': record.get('most_recent_date'),
                                'record_count': record.get('record_count', 1)
                            }
                            record_dicts.append(record_dict)
                            
                        except Exception as e:
                            logger.warning(f"Error processing record: {e}")
                            self.total_errors += 1
                    
                    # Insert in batches
                    for i in range(0, len(record_dicts), self.batch_size):
                        batch = record_dicts[i:i + self.batch_size]
                        cur.executemany(insert_sql, batch)
                        self.total_processed += len(batch)
                    
                    conn.commit()
                    logger.info(f"Successfully inserted {len(record_dicts)} location business records")
                    
        except Exception as e:
            logger.error(f"Error inserting processed records: {e}")
            raise
    
    def process_all_data(self, clear_existing: bool = True):
        """Process all business data using simple SOQL + Python processing"""
        start_time = time.time()
        
        try:
            # Create table if not exists
            self.create_table_if_not_exists()
            
            # Clear existing data if requested
            if clear_existing:
                self.clear_existing_data()
            
            # Fetch business data using simple SOQL
            logger.info("Step 1: Fetching business data with SOQL query...")
            business_data = self.fetch_business_data()
            
            # Group businesses by location
            logger.info("Step 2: Grouping businesses by location...")
            location_groups = self.group_businesses_by_location(business_data)
            
            # Process location groups
            logger.info("Step 3: Processing location groups and determining status...")
            processed_records = self.process_location_groups(location_groups)
            
            # Insert records
            logger.info("Step 4: Inserting processed records...")
            self.insert_processed_records(processed_records)
            
            # Final statistics
            end_time = time.time()
            duration = end_time - start_time
            
            # Count open/closed locations
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT is_open, COUNT(*) FROM location_business GROUP BY is_open")
                    status_counts = cur.fetchall()
                    open_count = sum(count for is_open, count in status_counts if is_open)
                    closed_count = sum(count for is_open, count in status_counts if not is_open)
            
            logger.info(f"Processing complete: {len(processed_records)} location records processed in {duration:.2f} seconds")
            logger.info(f"Open locations: {open_count}, Closed locations: {closed_count}")
            logger.info(f"Total errors: {self.total_errors}")
            
            return {
                'total_locations': len(processed_records),
                'open_locations': open_count,
                'closed_locations': closed_count,
                'total_errors': self.total_errors,
                'duration_seconds': duration
            }
            
        except Exception as e:
            logger.error(f"Error processing business data: {e}")
            raise

def main():
    """Main function for command-line usage"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Process business data using SOQL GROUP BY')
    parser.add_argument('--no-clear', action='store_true', help='Don\'t clear existing data')
    parser.add_argument('--db-url', help='Database connection string')
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Process data
    processor = SOQLBusinessProcessor(args.db_url)
    result = processor.process_all_data(clear_existing=not args.no_clear)
    
    print(f"Processing complete: {result}")

if __name__ == '__main__':
    main()
