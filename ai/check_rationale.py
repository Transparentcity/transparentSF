#!/usr/bin/env python3
import sys
import json
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from tools.db_utils import get_postgres_connection
import psycopg2.extras

def check_rationale(report_id):
    conn = get_postgres_connection()
    if not conn:
        print("Failed to connect to DB")
        return

    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    print(f"Checking rationale for report_id: {report_id}")
    cur.execute("""
        SELECT id, metric_name, metric_id, group_value, rationale 
        FROM monthly_reporting 
        WHERE report_id = %s
    """, (report_id,))
    
    rows = cur.fetchall()
    for row in rows:
        print(f"ID: {row['id']}, Metric: {row['metric_name']} ({row['metric_id']}), Group: '{row['group_value']}'")
        print(f"Rationale: {row['rationale'][:150] if row['rationale'] else '(empty)'}")
        print("-" * 40)
        
    cur.close()
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_rationale(sys.argv[1])
    else:
        print("Please provide report_id")

