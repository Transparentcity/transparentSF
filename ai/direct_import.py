#!/usr/bin/env python3
import os
import sys
sys.path.append(".")
from tools.db_utils import get_postgres_connection
import psycopg2.extras

# Update DATABASE_URL to point to new Cloud SQL instance
os.environ['DATABASE_URL'] = 'postgresql://transparentsf:0ffee68360f8c7b3a72ea6051a4d0fd0@34.28.89.105:5432/transparentsf'

print("Connecting to Cloud SQL instance...")
conn = get_postgres_connection()
cursor = conn.cursor()

print("Reading backup file...")
with open('transparentsf_backup_20250914_183525.sql', 'r') as f:
    sql_content = f.read()

print(f"SQL file size: {len(sql_content)} characters")
print("Executing SQL commands...")

try:
    # Split the SQL file into individual statements and execute them
    statements = sql_content.split(';')
    executed_count = 0
    
    for statement in statements:
        statement = statement.strip()
        if statement and not statement.startswith('--'):
            try:
                cursor.execute(statement)
                executed_count += 1
                if executed_count % 100 == 0:
                    print(f"Executed {executed_count} statements...")
            except Exception as e:
                print(f"Error executing statement: {e}")
                print(f"Statement: {statement[:100]}...")
    
    conn.commit()
    print(f"✅ Data import successful! Executed {executed_count} statements")
except Exception as e:
    print(f"❌ Import failed: {e}")
    conn.rollback()

cursor.close()
conn.close()
print("Import process completed")
