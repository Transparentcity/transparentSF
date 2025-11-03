#!/usr/bin/env python3
"""
Quick script to update zoning districts for all businesses.
Use this if update_zoning_districts() hasn't been run or needs to be re-run.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from ai.tools.simple_business_cache import SimpleBusinessCache

if __name__ == "__main__":
    print("=" * 80)
    print("UPDATING ZONING DISTRICTS FOR ALL BUSINESSES")
    print("=" * 80)
    print()
    
    cache = SimpleBusinessCache()
    result = cache.update_zoning_districts()
    
    print()
    print("=" * 80)
    print(f"COMPLETE: Updated {result:,} businesses with zoning districts")
    print("=" * 80)


