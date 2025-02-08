import requests
import json
from datetime import datetime, timedelta

# Test endpoint - using the emergency response dataset for response times
ENDPOINT = "2zdj-bwza"  # Emergency response endpoint
APP_TOKEN = None

BASE_URL = f"https://data.sfgov.org/resource/{ENDPOINT}.json"

def run_query(query):
    """Run a query and return results, handling errors"""
    try:
        params = {
            '$query': query
        }
        if APP_TOKEN:
            headers = {'X-App-Token': APP_TOKEN}
        else:
            headers = {}
            
        response = requests.get(BASE_URL, params=params, headers=headers)
        response.raise_for_status()
        results = response.json()
        return True, results
    except Exception as e:
        return False, str(e)

def test_date_function(description, query):
    """Test a date function and print results"""
    print(f"\n=== Testing: {description} ===")
    print(f"Query: {query}")
    success, results = run_query(query)
    if success:
        print(f"Success! First few results:")
        print(json.dumps(results[:2], indent=2))
    else:
        print(f"Failed: {results}")

# Test queries for response time calculations
tests = [
    (
        "Testing hour and minute extraction",
        """
        SELECT 
            received_datetime,
            date_extract_hh(received_datetime) as hour,
            date_extract_mm(received_datetime) as minute
        WHERE 
            received_datetime IS NOT NULL 
        LIMIT 5
        """
    ),
    (
        "Same day response time calculation",
        """
        SELECT 
            received_datetime,
            onscene_datetime,
            CASE 
                WHEN date_extract_y(received_datetime) = date_extract_y(onscene_datetime)
                AND date_extract_m(received_datetime) = date_extract_m(onscene_datetime)
                AND date_extract_d(received_datetime) = date_extract_d(onscene_datetime)
                THEN (
                    (date_extract_hh(onscene_datetime) * 60 + date_extract_mm(onscene_datetime)) -
                    (date_extract_hh(received_datetime) * 60 + date_extract_mm(received_datetime))
                )
            END as response_time_minutes
        WHERE 
            received_datetime IS NOT NULL 
            AND onscene_datetime IS NOT NULL
            AND received_datetime < onscene_datetime
        LIMIT 5
        """
    ),
    (
        "Full response time calculation",
        """
        SELECT 
            received_datetime,
            onscene_datetime,
            (
                (date_extract_y(onscene_datetime) - date_extract_y(received_datetime)) * 525600 +
                (date_extract_m(onscene_datetime) - date_extract_m(received_datetime)) * 43800 +
                (date_extract_d(onscene_datetime) - date_extract_d(received_datetime)) * 1440 +
                (date_extract_hh(onscene_datetime) * 60 + date_extract_mm(onscene_datetime)) -
                (date_extract_hh(received_datetime) * 60 + date_extract_mm(received_datetime))
            ) as total_minutes
        WHERE 
            received_datetime IS NOT NULL 
            AND onscene_datetime IS NOT NULL
            AND received_datetime < onscene_datetime
        LIMIT 5
        """
    ),
    (
        "Average response time by hour of day",
        """
        SELECT 
            date_extract_hh(received_datetime) as hour_received,
            avg(
                CASE 
                    WHEN date_extract_y(received_datetime) = date_extract_y(onscene_datetime)
                    AND date_extract_m(received_datetime) = date_extract_m(onscene_datetime)
                    AND date_extract_d(received_datetime) = date_extract_d(onscene_datetime)
                    THEN (
                        (date_extract_hh(onscene_datetime) * 60 + date_extract_mm(onscene_datetime)) -
                        (date_extract_hh(received_datetime) * 60 + date_extract_mm(received_datetime))
                    )
                END
            ) as avg_response_minutes
        WHERE 
            received_datetime IS NOT NULL 
            AND onscene_datetime IS NOT NULL
            AND received_datetime < onscene_datetime
        GROUP BY hour_received
        ORDER BY hour_received
        """
    )
]

def main():
    print("Starting response time calculation tests...")
    for description, query in tests:
        test_date_function(description, query)

if __name__ == "__main__":
    main() 