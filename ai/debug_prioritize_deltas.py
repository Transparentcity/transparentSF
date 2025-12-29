#!/usr/bin/env python3
"""
Debug script to test prioritize_deltas and store_prioritized_items functions
without running the full report generation process.

Usage:
    python3 debug_prioritize_deltas.py [district] [period_type]
    
Example:
    python3 debug_prioritize_deltas.py 11 month
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

# Import after path setup
from ai.monthly_report import (
    select_deltas_to_discuss,
    select_anomalies_to_discuss,
    prioritize_deltas,
    store_prioritized_items
)
import logging

# Configure logging to be very verbose for debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('debug_prioritize.log', mode='w', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

def debug_prioritize_deltas(district="11", period_type="month"):
    """Debug the prioritize_deltas process."""
    
    print("=" * 80)
    print(f"DEBUG: Testing prioritize_deltas for District {district}, Period: {period_type}")
    print("=" * 80)
    print()
    
    # Step 1: Select deltas
    print("Step 1: Selecting deltas to discuss...")
    deltas_result = select_deltas_to_discuss(period_type=period_type, district=district)
    if deltas_result.get("status") != "success":
        print(f"❌ Error selecting deltas: {deltas_result.get('message')}")
        return
    
    deltas = deltas_result.get("deltas", [])
    print(f"✅ Found {len(deltas)} deltas")
    print()
    
    # Step 2: Select anomalies
    print("Step 2: Selecting anomalies to discuss...")
    anomalies = select_anomalies_to_discuss(period_type=period_type, district=district, limit=20)
    print(f"✅ Found {len(anomalies)} anomalies")
    print()
    
    # Step 3: Prioritize deltas
    print("Step 3: Prioritizing deltas (this may take a while)...")
    print("-" * 80)
    prioritized_result = prioritize_deltas(
        deltas, 
        anomalies=anomalies if anomalies else None, 
        max_items=10,  # Not used anymore, but kept for compatibility
        model_key=None
    )
    
    if prioritized_result.get("status") != "success":
        print(f"❌ Error prioritizing deltas: {prioritized_result.get('message')}")
        return
    
    print("-" * 80)
    print()
    
    # Extract results
    prioritized_items = prioritized_result.get("prioritized_items", [])
    causal_graph_analysis = prioritized_result.get("causal_graph_analysis")
    research_agendas = prioritized_result.get("research_agendas")
    
    print("=" * 80)
    print("RESULTS FROM prioritize_deltas:")
    print("=" * 80)
    print(f"✅ Prioritized Items: {len(prioritized_items)}")
    for i, item in enumerate(prioritized_items, 1):
        print(f"  {i}. {item.get('metric')} (ID: {item.get('metric_id')}, Priority: {item.get('priority')})")
    print()
    
    print(f"✅ Causal Graph Analysis: {bool(causal_graph_analysis)}")
    if causal_graph_analysis:
        print(f"   Keys: {list(causal_graph_analysis.keys())}")
        if 'detected_chains' in causal_graph_analysis:
            print(f"   Detected Chains: {len(causal_graph_analysis.get('detected_chains', []))}")
        if 'unexpected_relationships' in causal_graph_analysis:
            print(f"   Unexpected Relationships: {len(causal_graph_analysis.get('unexpected_relationships', []))}")
    print()
    
    print(f"✅ Research Agendas: {bool(research_agendas)}")
    if research_agendas:
        print(f"   Number of agendas: {len(research_agendas)}")
        for i, agenda in enumerate(research_agendas, 1):
            print(f"   Agenda {i}:")
            print(f"     Narrative: {agenda.get('narrative_thread', 'N/A')[:80]}...")
            print(f"     Research Questions: {len(agenda.get('research_questions', []))}")
            suggested_metrics = agenda.get('suggested_metrics', [])
            print(f"     Suggested Metrics: {len(suggested_metrics)}")
            for j, metric in enumerate(suggested_metrics, 1):
                has_rq = 'research_question' in metric and metric.get('research_question')
                print(f"       {j}. {metric.get('metric_name', 'N/A')} (ID: {metric.get('metric_id', 'N/A')})")
                print(f"          Has research_question: {'✅' if has_rq else '❌'}")
                if has_rq:
                    print(f"          Research Question: {metric['research_question'][:80]}...")
    else:
        print("   ❌ No research agendas returned!")
    print()
    
    # Step 4: Test storing (but don't actually commit to database)
    print("=" * 80)
    print("TESTING store_prioritized_items (DRY RUN - checking what would be stored):")
    print("=" * 80)
    print()
    
    print(f"🔍 DEBUG: About to call store_prioritized_items with:")
    print(f"   - {len(prioritized_items)} prioritized items")
    print(f"   - causal_graph_analysis: {bool(causal_graph_analysis)}")
    print(f"   - research_agendas: {bool(research_agendas)}")
    if research_agendas:
        print(f"   - research_agendas type: {type(research_agendas)}")
        print(f"   - research_agendas length: {len(research_agendas)}")
        print(f"   - research_agendas content preview: {json.dumps(research_agendas)[:200]}...")
    print()
    
    # Save results to JSON file for inspection
    output_file = f"debug_prioritize_output_{district}_{period_type}.json"
    output_data = {
        "prioritized_items": prioritized_items,
        "causal_graph_analysis": causal_graph_analysis,
        "research_agendas": research_agendas,
        "deltas_count": len(deltas),
        "anomalies_count": len(anomalies)
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, default=str)
    
    print(f"✅ Results saved to: {output_file}")
    print()
    print("=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"✅ prioritize_deltas returned {len(prioritized_items)} items")
    print(f"{'✅' if causal_graph_analysis else '❌'} Causal graph analysis: {bool(causal_graph_analysis)}")
    print(f"{'✅' if research_agendas else '❌'} Research agendas: {bool(research_agendas)} ({len(research_agendas) if research_agendas else 0} agendas)")
    
    if research_agendas:
        total_suggested_metrics = sum(len(a.get('suggested_metrics', [])) for a in research_agendas)
        metrics_with_rq = 0
        for agenda in research_agendas:
            for metric in agenda.get('suggested_metrics', []):
                if 'research_question' in metric and metric.get('research_question'):
                    metrics_with_rq += 1
        print(f"   - Total suggested metrics: {total_suggested_metrics}")
        print(f"   - Metrics with research_question: {metrics_with_rq}/{total_suggested_metrics}")
    
    print()
    print("Next step: Check if store_prioritized_items is being called with these values")
    print("          and if research_agendas are being stored in the metadata")

if __name__ == "__main__":
    district = sys.argv[1] if len(sys.argv) > 1 else "11"
    period_type = sys.argv[2] if len(sys.argv) > 2 else "month"
    
    try:
        debug_prioritize_deltas(district=district, period_type=period_type)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)











