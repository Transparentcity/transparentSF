#!/usr/bin/env python3
"""
Script to check what was stored in the database for a monthly report.
Usage: python check_report_data.py [report_id] [district]
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.db_utils import get_postgres_connection
import psycopg2.extras

def check_report_data(report_id=None, district=None):
    """Check what's stored in the database for a report."""
    conn = get_postgres_connection()
    if not conn:
        print("❌ Failed to connect to database")
        return
    
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Find the report
    if report_id:
        cur.execute("""
            SELECT id, district, period_type, max_items, created_at, 
                   original_filename, revised_filename, metadata
            FROM reports
            WHERE id = %s
        """, (report_id,))
    elif district is not None:
        cur.execute("""
            SELECT id, district, period_type, max_items, created_at, 
                   original_filename, revised_filename, metadata
            FROM reports
            WHERE district = %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (district,))
    else:
        cur.execute("""
            SELECT id, district, period_type, max_items, created_at, 
                   original_filename, revised_filename, metadata
            FROM reports
            ORDER BY created_at DESC
            LIMIT 1
        """)
    
    report = cur.fetchone()
    
    if not report:
        print("❌ No report found")
        cur.close()
        conn.close()
        return
    
    report_id = report['id']
    print(f"\n{'='*80}")
    print(f"REPORT ID: {report_id}")
    print(f"District: {report['district']}")
    print(f"Period Type: {report['period_type']}")
    print(f"Max Items: {report['max_items']}")
    print(f"Created: {report['created_at']}")
    print(f"Filename: {report['original_filename']}")
    print(f"{'='*80}\n")
    
    # Parse metadata
    metadata = report.get('metadata')
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except:
            metadata = {}
    elif metadata is None:
        metadata = {}
    
    print("📋 REPORT METADATA:")
    print(json.dumps(metadata, indent=2))
    print()
    
    # Check for research_agendas in metadata
    if 'research_agendas' in metadata:
        print(f"✅ Found {len(metadata['research_agendas'])} research agenda(s) in metadata")
        for idx, agenda in enumerate(metadata['research_agendas']):
            print(f"\n  Agenda {idx + 1}:")
            print(f"    Narrative: {agenda.get('narrative_thread', 'N/A')[:100]}...")
            print(f"    Suggested Metrics: {len(agenda.get('suggested_metrics', []))}")
            for metric in agenda.get('suggested_metrics', []):
                has_rq = 'research_question' in metric and metric.get('research_question')
                print(f"      - {metric.get('metric_name', 'N/A')} (ID: {metric.get('metric_id', 'N/A')})")
                print(f"        Has research_question: {'✅' if has_rq else '❌'}")
                if has_rq:
                    print(f"        Research Question: {metric['research_question'][:80]}...")
    else:
        print("❌ No research_agendas found in metadata")
    
    print()
    
    # Get all items from monthly_reporting
    cur.execute("""
        SELECT id, item_title, metric_name, metric_id, group_value, 
               priority, rationale, explanation, metadata,
               comparison_mean, recent_mean, difference, percent_change
        FROM monthly_reporting
        WHERE report_id = %s
        ORDER BY priority
    """, (report_id,))
    
    items = cur.fetchall()
    
    print(f"📊 MONTHLY_REPORTING ITEMS: {len(items)} total")
    print(f"{'='*80}\n")
    
    for item in items:
        print(f"Item ID: {item['id']}")
        print(f"  Title: {item['item_title']}")
        print(f"  Metric: {item['metric_name']} (ID: {item['metric_id']})")
        print(f"  Group: {item['group_value']}")
        print(f"  Priority: {item['priority']}")
        print(f"  Rationale: {item['rationale'][:100] if item['rationale'] else 'None'}...")
        print(f"  Has Explanation: {'✅' if item['explanation'] else '❌'}")
        
        # Check metadata
        item_metadata = item.get('metadata')
        if isinstance(item_metadata, str):
            try:
                item_metadata = json.loads(item_metadata)
            except:
                item_metadata = {}
        elif item_metadata is None:
            item_metadata = {}
        
        if 'from_research_agenda' in item_metadata:
            print(f"  ⭐ From Research Agenda: Yes")
            print(f"    Agenda Index: {item_metadata.get('agenda_index', 'N/A')}")
            print(f"    Research Question: {item_metadata.get('research_question', 'N/A')[:80]}...")
        
        print()
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    report_id = None
    district = None
    
    if len(sys.argv) > 1:
        if sys.argv[1].isdigit():
            report_id = int(sys.argv[1])
        else:
            district = sys.argv[1]
    
    if len(sys.argv) > 2:
        district = sys.argv[2]
    
    check_report_data(report_id=report_id, district=district)



