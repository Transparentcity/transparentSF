#!/usr/bin/env python3
"""
Database Connection Verifier

This script verifies that the application is connecting to the correct remote database
and not accidentally using a local database. It should be run before any database operations
to ensure we're using the production database.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def verify_database_connection():
    """
    Verify that we're connecting to the correct remote database.
    Returns True if using remote DB, False if using local DB.
    """
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        print("❌ ERROR: DATABASE_URL not found in environment variables")
        print("   Make sure ai/.env file exists and contains DATABASE_URL")
        return False
    
    # Check if it's pointing to localhost
    if "localhost" in database_url or "127.0.0.1" in database_url:
        print("❌ ERROR: DATABASE_URL is pointing to localhost!")
        print(f"   Current DATABASE_URL: {database_url}")
        print("   This should point to the remote database at 34.28.89.105")
        return False
    
    # Check if it's pointing to the correct remote database
    if "34.28.89.105" not in database_url:
        print("⚠️  WARNING: DATABASE_URL is not pointing to the expected remote database")
        print(f"   Current DATABASE_URL: {database_url}")
        print("   Expected to contain: 34.28.89.105")
        return False
    
    print("✅ Database connection verified - using remote database")
    print(f"   DATABASE_URL: {database_url}")
    return True

def test_actual_connection():
    """
    Test the actual database connection to ensure it's working.
    """
    try:
        from db_utils import execute_with_connection
        
        def get_connection_info(conn):
            cur = conn.cursor()
            cur.execute('SELECT current_database(), inet_server_addr(), inet_server_port()')
            result = cur.fetchone()
            cur.close()
            return result
        
        result = execute_with_connection(get_connection_info)
        
        if result["status"] == "success":
            db_name, server_ip, server_port = result["result"]
            print(f"✅ Database connection successful")
            print(f"   Database: {db_name}")
            print(f"   Server IP: {server_ip}")
            print(f"   Server Port: {server_port}")
            
            # Verify it's the remote database
            if server_ip == "34.28.89.105":
                print("✅ Confirmed: Connected to remote database")
                return True
            else:
                print(f"❌ ERROR: Connected to wrong database at {server_ip}")
                return False
        else:
            print(f"❌ Database connection failed: {result['message']}")
            return False
            
    except Exception as e:
        print(f"❌ Database connection test failed: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Verifying database connection...")
    print("=" * 50)
    
    # Check environment configuration
    env_ok = verify_database_connection()
    
    if env_ok:
        print("\n🔗 Testing actual database connection...")
        print("-" * 30)
        
        # Test actual connection
        connection_ok = test_actual_connection()
        
        if connection_ok:
            print("\n✅ All database connection checks passed!")
            print("   The application is correctly configured to use the remote database.")
            sys.exit(0)
        else:
            print("\n❌ Database connection test failed!")
            sys.exit(1)
    else:
        print("\n❌ Database configuration is incorrect!")
        sys.exit(1)
