"""
Exact Real-Time Vacancy Processor

This processor uses the EXACT same logic as the true real-time vacancy analysis,
but stores the results in a database table for better performance.

This should produce identical results to the true real-time version.
"""

import logging
import time
import psycopg2
import psycopg2.extras
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import json
import os
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv
from ai.tools.data_fetcher import fetch_data_from_api

# Load environment variables
load_dotenv('ai/.env')

logger = logging.getLogger(__name__)

@dataclass
class VacancyLocation:
    """Represents a processed vacancy location"""
    address_key: str
    full_business_address: str
    latitude: float
    longitude: float
    dba_name: str
    naic_code_description: str
    lic_code_description: str
    business_corridor: str
    supervisor_district: str
    most_recent_open_date: Optional[datetime]
    most_recent_close_date: Optional[datetime]
    status: str
    business_count: int
    all_business_names: List[str]

class ExactRealtimeProcessor:
    """Processor that uses exact same logic as true real-time analysis"""
    
    def __init__(self):
        self.db_url = os.getenv('DATABASE_URL')
        if not self.db_url:
            raise ValueError("DATABASE_URL not found in environment")
    
    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(self.db_url)
    
    def create_table(self):
        """Create the exact_realtime_vacancy table"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    create_sql = """
                    CREATE TABLE IF NOT EXISTS exact_realtime_vacancy (
                        id SERIAL PRIMARY KEY,
                        -- Location identifier
                        address_key TEXT UNIQUE,
                        full_business_address TEXT,
                        latitude FLOAT,
                        longitude FLOAT,
                        
                        -- Business data (from primary business)
                        dba_name TEXT,
                        naic_code_description TEXT,
                        lic_code_description TEXT,
                        business_corridor TEXT,
                        supervisor_district TEXT,
                        
                        -- Status determination
                        most_recent_open_date TIMESTAMP,
                        most_recent_close_date TIMESTAMP,
                        status TEXT,
                        
                        -- Business count and names
                        business_count INTEGER,
                        all_business_names TEXT[],  -- Array of all business names
                        
                        -- Metadata
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    
                    -- Create indexes for performance
                    CREATE INDEX IF NOT EXISTS idx_exact_realtime_vacancy_address_key ON exact_realtime_vacancy(address_key);
                    CREATE INDEX IF NOT EXISTS idx_exact_realtime_vacancy_status ON exact_realtime_vacancy(status);
                    CREATE INDEX IF NOT EXISTS idx_exact_realtime_vacancy_district ON exact_realtime_vacancy(supervisor_district);
                    CREATE INDEX IF NOT EXISTS idx_exact_realtime_vacancy_corridor ON exact_realtime_vacancy(business_corridor);
                    CREATE INDEX IF NOT EXISTS idx_exact_realtime_vacancy_coords ON exact_realtime_vacancy(latitude, longitude);
                    """
                    
                    cur.execute(create_sql)
                    conn.commit()
                    logger.info("Created exact_realtime_vacancy table")
                    
        except Exception as e:
            logger.error(f"Error creating table: {e}")
            raise
    
    def parse_date(self, date_str):
        """Parse date string - EXACT COPY from old code"""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str.split('T')[0], '%Y-%m-%d')
        except:
            return None
    
    def parse_location_coordinates(self, location_data):
        """Extract coordinates from location field - EXACT COPY from old code"""
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
    
    def process_in_batches(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """Process data in smaller batches to avoid timeouts"""
        logger.info("Starting batch processing to avoid timeouts")
        start_time = time.time()
        
        all_locations = []
        batch_size = 10000
        offset = 0
        total_processed = 0
        
        while True:
            # Build query for this batch
            batch_query = """
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
            ORDER BY certificate_number
            LIMIT {batch_size} OFFSET {offset}
            """.format(batch_size=batch_size, offset=offset)
            
            logger.info(f"Processing batch: offset={offset}, limit={batch_size}")
            
            try:
                result = fetch_data_from_api({
                    'endpoint': 'g8m3-pdis',
                    'query': batch_query
                })
                
                if not result or 'data' not in result:
                    logger.warning(f"No data returned for batch at offset {offset}")
                    break
                
                batch_data = result['data']
                if not batch_data:
                    logger.info("No more data, processing complete")
                    break
                
                logger.info(f"Fetched {len(batch_data)} records for batch at offset {offset}")
                
                # Process this batch
                batch_locations = self.process_batch_data(batch_data)
                all_locations.extend(batch_locations)
                
                total_processed += len(batch_data)
                logger.info(f"Processed {len(batch_locations)} locations from {len(batch_data)} records (total: {len(all_locations)})")
                
                # Check if we've hit the limit
                if limit and total_processed >= limit:
                    logger.info(f"Reached limit of {limit} records")
                    break
                
                # If we got fewer records than batch_size, we're done
                if len(batch_data) < batch_size:
                    logger.info("Got fewer records than batch size, processing complete")
                    break
                
                offset += batch_size
                
            except Exception as e:
                logger.error(f"Error processing batch at offset {offset}: {e}")
                break
        
        # Store all locations
        self.store_locations(all_locations)
        
        # Calculate summary
        total_locations = len(all_locations)
        open_locations = len([d for d in all_locations if d.status == 'Open'])
        closed_locations = len([d for d in all_locations if d.status == 'Closed'])
        processing_time = time.time() - start_time
        
        logger.info(f"Batch processing completed in {processing_time:.2f} seconds")
        logger.info(f"Processed {total_locations} locations: {open_locations} open, {closed_locations} closed")
        
        return {
            "status": "success",
            "total_locations": total_locations,
            "open_locations": open_locations,
            "closed_locations": closed_locations,
            "processing_time": processing_time
        }
    
    def process_batch_data(self, raw_data: List[Dict]) -> List[VacancyLocation]:
        """Process a batch of raw data into VacancyLocation objects"""
        # Group data by address - same logic as main process_data method
        address_groups = defaultdict(list)
        
        for record in raw_data:
            address_key = record.get('full_business_address')
            if not address_key:
                coordinates = self.parse_location_coordinates(record.get('location'))
                address_key = f"{coordinates[0]:.6f},{coordinates[1]:.6f}"
            
            address_groups[address_key].append(record)
        
        processed_locations = []
        
        # Process each address group
        for address_key, businesses in address_groups.items():
            # Find all open and close dates for this address
            all_open_dates = []
            all_close_dates = []
            business_names = []
            
            # Use the first business for location and address info
            primary_business = businesses[0]
            coordinates = self.parse_location_coordinates(primary_business.get('location'))
            
            for business in businesses:
                business_names.append(business.get('dba_name', 'Unknown Business'))
                
                # Collect all dates from this business
                dba_start = self.parse_date(business.get('dba_start_date'))
                location_start = self.parse_date(business.get('location_start_date'))
                dba_end = self.parse_date(business.get('dba_end_date'))
                location_end = self.parse_date(business.get('location_end_date'))
                
                # Add valid dates to collections
                if dba_start:
                    all_open_dates.append(dba_start)
                if location_start:
                    all_open_dates.append(location_start)
                if dba_end:
                    all_close_dates.append(dba_end)
                if location_end:
                    all_close_dates.append(location_end)
            
            # Find most recent dates across all businesses at this address
            most_recent_open_date = max(all_open_dates) if all_open_dates else None
            most_recent_close_date = max(all_close_dates) if all_close_dates else None
            
            # Determine address status
            if not most_recent_close_date:
                status = 'Open'
            elif not most_recent_open_date:
                status = 'Closed'  
            elif most_recent_close_date > most_recent_open_date:
                status = 'Closed'
            else:
                status = 'Open'
            
            # Create a representative business name
            if len(business_names) == 1:
                display_name = business_names[0]
            else:
                display_name = f"{business_names[0]} (+{len(business_names)-1} others)"
            
            location = VacancyLocation(
                address_key=address_key,
                full_business_address=primary_business.get('full_business_address'),
                latitude=coordinates[1],
                longitude=coordinates[0],
                dba_name=display_name,
                naic_code_description=primary_business.get('naic_code_description'),
                lic_code_description=primary_business.get('lic_code_description'),
                business_corridor=primary_business.get('business_corridor'),
                supervisor_district=primary_business.get('supervisor_district'),
                most_recent_open_date=most_recent_open_date,
                most_recent_close_date=most_recent_close_date,
                status=status,
                business_count=len(businesses),
                all_business_names=business_names
            )
            
            processed_locations.append(location)
        
        return processed_locations

    def process_data(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """Process vacancy data using EXACT same logic as true real-time analysis"""
        logger.info("Starting exact real-time vacancy data processing")
        start_time = time.time()
        
        # Build the SOQL query for DataSF API - EXACT COPY from old code
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
        
        # Add limit - EXACT COPY from old code
        if limit:
            base_query += f" LIMIT {limit}"
        else:
            base_query += " LIMIT 50000"  # Same limit as working code
        
        try:
            # Process in smaller batches to avoid timeouts
            if not limit or limit > 10000:
                logger.info("Processing in batches of 10,000 to avoid timeouts")
                return self.process_in_batches(limit)
            
            result = fetch_data_from_api({
                'endpoint': 'g8m3-pdis',
                'query': base_query
            })
            
            if not result or 'data' not in result:
                error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
                raise Exception(f"Failed to fetch data from DataSF: {error_msg}")
            
            raw_data = result['data']
            logger.info(f"Fetched {len(raw_data)} raw business records")
            
            # Group data by address and process vacancy status per address - EXACT COPY from old code
            address_groups = defaultdict(list)
            
            for record in raw_data:
                # Use full_business_address as the grouping key, fall back to coordinates
                address_key = record.get('full_business_address')
                if not address_key:
                    # Fall back to coordinates for grouping
                    coordinates = self.parse_location_coordinates(record.get('location'))
                    address_key = f"{coordinates[0]:.6f},{coordinates[1]:.6f}"
                
                address_groups[address_key].append(record)
            
            logger.info(f"Grouped into {len(address_groups)} unique addresses")
            
            processed_locations = []
            
            # Process each address group - EXACT COPY from old code
            for address_key, businesses in address_groups.items():
                # Find all open and close dates for this address
                all_open_dates = []
                all_close_dates = []
                
                # Collect all business names at this address
                business_names = []
                
                # Use the first business for location and address info
                primary_business = businesses[0]
                coordinates = self.parse_location_coordinates(primary_business.get('location'))
                
                for business in businesses:
                    business_names.append(business.get('dba_name', 'Unknown Business'))
                    
                    # Collect all dates from this business
                    dba_start = self.parse_date(business.get('dba_start_date'))
                    location_start = self.parse_date(business.get('location_start_date'))
                    dba_end = self.parse_date(business.get('dba_end_date'))
                    location_end = self.parse_date(business.get('location_end_date'))
                    
                    # Add valid dates to collections
                    if dba_start:
                        all_open_dates.append(dba_start)
                    if location_start:
                        all_open_dates.append(location_start)
                    if dba_end:
                        all_close_dates.append(dba_end)
                    if location_end:
                        all_close_dates.append(location_end)
                
                # Find most recent dates across all businesses at this address
                most_recent_open_date = max(all_open_dates) if all_open_dates else None
                most_recent_close_date = max(all_close_dates) if all_close_dates else None
                
                # Determine address status: Open if most recent activity was an opening - EXACT COPY from old code
                if not most_recent_close_date:
                    status = 'Open'
                elif not most_recent_open_date:
                    status = 'Closed'  
                elif most_recent_close_date > most_recent_open_date:
                    status = 'Closed'
                else:
                    status = 'Open'
                
                # Create a representative business name (primary or combined) - EXACT COPY from old code
                if len(business_names) == 1:
                    display_name = business_names[0]
                else:
                    display_name = f"{business_names[0]} (+{len(business_names)-1} others)"
                
                location = VacancyLocation(
                    address_key=address_key,
                    full_business_address=primary_business.get('full_business_address'),
                    latitude=coordinates[1],
                    longitude=coordinates[0],
                    dba_name=display_name,
                    naic_code_description=primary_business.get('naic_code_description'),
                    lic_code_description=primary_business.get('lic_code_description'),
                    business_corridor=primary_business.get('business_corridor'),
                    supervisor_district=primary_business.get('supervisor_district'),
                    most_recent_open_date=most_recent_open_date,
                    most_recent_close_date=most_recent_close_date,
                    status=status,
                    business_count=len(businesses),
                    all_business_names=business_names
                )
                
                processed_locations.append(location)
            
            # Store in database
            self.store_locations(processed_locations)
            
            # Calculate summary statistics
            total_locations = len(processed_locations)
            open_locations = len([d for d in processed_locations if d.status == 'Open'])
            closed_locations = len([d for d in processed_locations if d.status == 'Closed'])
            
            processing_time = time.time() - start_time
            
            logger.info(f"Processing completed in {processing_time:.2f} seconds")
            logger.info(f"Processed {total_locations} locations: {open_locations} open, {closed_locations} closed")
            
            return {
                "status": "success",
                "total_locations": total_locations,
                "open_locations": open_locations,
                "closed_locations": closed_locations,
                "processing_time": processing_time
            }
            
        except Exception as e:
            logger.error(f"Error processing data: {e}")
            raise
    
    def store_locations(self, locations: List[VacancyLocation]):
        """Store processed locations in database"""
        logger.info(f"Storing {len(locations)} locations in database")
        
        try:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Clear existing data
                    cur.execute("DELETE FROM exact_realtime_vacancy")
                    
                    # Insert new data
                    insert_sql = """
                    INSERT INTO exact_realtime_vacancy (
                        address_key, full_business_address, latitude, longitude,
                        dba_name, naic_code_description, lic_code_description,
                        business_corridor, supervisor_district,
                        most_recent_open_date, most_recent_close_date, status,
                        business_count, all_business_names
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """
                    
                    for location in locations:
                        cur.execute(insert_sql, (
                            location.address_key,
                            location.full_business_address,
                            location.latitude,
                            location.longitude,
                            location.dba_name,
                            location.naic_code_description,
                            location.lic_code_description,
                            location.business_corridor,
                            location.supervisor_district,
                            location.most_recent_open_date,
                            location.most_recent_close_date,
                            location.status,
                            location.business_count,
                            location.all_business_names
                        ))
                    
                    conn.commit()
                    logger.info(f"Successfully stored {len(locations)} locations")
                    
        except Exception as e:
            logger.error(f"Error storing locations: {e}")
            raise
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the exact real-time vacancy data"""
        try:
            with self.get_db_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Get total counts
                    cur.execute("""
                        SELECT 
                            COUNT(*) as total_locations,
                            COUNT(CASE WHEN status = 'Open' THEN 1 END) as open_locations,
                            COUNT(CASE WHEN status = 'Closed' THEN 1 END) as closed_locations
                        FROM exact_realtime_vacancy
                    """)
                    
                    stats = cur.fetchone()
                    
                    # Get district breakdown
                    cur.execute("""
                        SELECT 
                            supervisor_district,
                            COUNT(*) as count
                        FROM exact_realtime_vacancy
                        WHERE supervisor_district IS NOT NULL
                        GROUP BY supervisor_district
                        ORDER BY count DESC
                        LIMIT 10
                    """)
                    
                    district_stats = cur.fetchall()
                    
                    # Get corridor breakdown
                    cur.execute("""
                        SELECT 
                            business_corridor,
                            COUNT(*) as count
                        FROM exact_realtime_vacancy
                        WHERE business_corridor IS NOT NULL AND business_corridor != ''
                        GROUP BY business_corridor
                        ORDER BY count DESC
                        LIMIT 10
                    """)
                    
                    corridor_stats = cur.fetchall()
                    
                    return {
                        "total_locations": stats['total_locations'],
                        "open_locations": stats['open_locations'],
                        "closed_locations": stats['closed_locations'],
                        "top_districts": {str(row['supervisor_district']): row['count'] for row in district_stats},
                        "top_corridors": {row['business_corridor']: row['count'] for row in corridor_stats}
                    }
                    
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            raise

def main():
    """Test the exact real-time processor"""
    processor = ExactRealtimeProcessor()
    
    # Create table
    processor.create_table()
    
    # Process data with small limit for testing
    result = processor.process_data(limit=1000)
    print(f"Processing result: {result}")
    
    # Get stats
    stats = processor.get_stats()
    print(f"Stats: {stats}")

if __name__ == "__main__":
    main()
