#!/usr/bin/env python3
"""
Qdrant Management Script for TransparentSF
Provides utilities to manage Qdrant vector database in production.
"""

import os
import sys
import argparse
import requests
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def check_qdrant_health():
    """Check if Qdrant is running and healthy."""
    qdrant_host = os.getenv("QDRANT_URL", "localhost")
    qdrant_port = os.getenv("QDRANT_PORT", "6333")
    
    try:
        response = requests.get(f"http://{qdrant_host}:{qdrant_port}/healthz", timeout=5)
        if response.status_code == 200:
            print(f"✅ Qdrant is healthy at {qdrant_host}:{qdrant_port}")
            return True
        else:
            print(f"❌ Qdrant health check failed with status {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Cannot connect to Qdrant at {qdrant_host}:{qdrant_port}: {e}")
        return False

def get_collections():
    """Get list of all collections in Qdrant."""
    qdrant_host = os.getenv("QDRANT_URL", "localhost")
    qdrant_port = os.getenv("QDRANT_PORT", "6333")
    
    try:
        response = requests.get(f"http://{qdrant_host}:{qdrant_port}/collections", timeout=10)
        if response.status_code == 200:
            data = response.json()
            collections = data.get('result', {}).get('collections', [])
            print(f"📚 Found {len(collections)} collections:")
            for collection in collections:
                name = collection.get('name', 'Unknown')
                points_count = collection.get('points_count', 0)
                print(f"  - {name}: {points_count} points")
            return collections
        else:
            print(f"❌ Failed to get collections: {response.status_code}")
            return []
    except requests.exceptions.RequestException as e:
        print(f"❌ Error getting collections: {e}")
        return []

def wait_for_qdrant(max_wait=60):
    """Wait for Qdrant to become available."""
    print(f"⏳ Waiting for Qdrant to become available (max {max_wait}s)...")
    
    for i in range(max_wait):
        if check_qdrant_health():
            print("✅ Qdrant is ready!")
            return True
        time.sleep(1)
        if i % 10 == 0 and i > 0:
            print(f"   Still waiting... ({i}s elapsed)")
    
    print("❌ Qdrant did not become available within the timeout period")
    return False

def main():
    parser = argparse.ArgumentParser(description="Qdrant Management Script")
    parser.add_argument("command", choices=["health", "collections", "wait"], 
                       help="Command to execute")
    parser.add_argument("--wait-time", type=int, default=60,
                       help="Maximum wait time in seconds for 'wait' command")
    
    args = parser.parse_args()
    
    if args.command == "health":
        check_qdrant_health()
    elif args.command == "collections":
        get_collections()
    elif args.command == "wait":
        wait_for_qdrant(args.wait_time)

if __name__ == "__main__":
    main()
