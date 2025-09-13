#!/usr/bin/env python3
"""
Google Cloud SQL migration script for TransparentSF.

This script helps migrate the existing PostgreSQL database to Google Cloud SQL,
including data export, Cloud SQL setup, and data import.
"""

import os
import sys
import subprocess
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import json

# Add the parent directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.db_utils import get_postgres_connection

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cloud_sql_migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class CloudSQLMigrator:
    """Handles migration of PostgreSQL database to Google Cloud SQL."""
    
    def __init__(self, project_id: str, instance_name: str, database_name: str = "transparentsf"):
        """
        Initialize the Cloud SQL migrator.
        
        Args:
            project_id: Google Cloud project ID
            instance_name: Cloud SQL instance name
            database_name: Database name (defaults to transparentsf)
        """
        self.project_id = project_id
        self.instance_name = instance_name
        self.database_name = database_name
        self.backup_file = f"transparentsf_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
        
    def check_prerequisites(self) -> bool:
        """Check if required tools are installed."""
        logger.info("Checking prerequisites...")

        # Check if gcloud is installed
        try:
            result = subprocess.run(['gcloud', '--version'], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("gcloud CLI not found. Please install Google Cloud SDK.")
                return False
            logger.info("✓ gcloud CLI found")
        except FileNotFoundError:
            logger.error("gcloud CLI not found. Please install Google Cloud SDK.")
            return False

        # Check if local backup file exists
        backup_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'temp', 'database_backup_20250912_022232.sql')
        if not os.path.exists(backup_path):
            logger.error(f"Local backup file not found: {backup_path}")
            return False
        logger.info("✓ Local backup file found")

        return True
    
    def export_database(self) -> bool:
        """Use local backup file instead of exporting from database."""
        # Look for local backup file
        backup_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'temp', 'database_backup_20250912_022232.sql')

        if os.path.exists(backup_path):
            logger.info(f"Using local backup file: {backup_path}")
            # Copy the backup file to our working directory
            import shutil
            shutil.copy2(backup_path, self.backup_file)
            logger.info(f"✓ Local backup copied to {self.backup_file}")
            return True
        else:
            logger.error(f"Local backup file not found: {backup_path}")
            return False
    
    def create_cloud_sql_instance(self, region: str = "us-central1", 
                                 machine_type: str = "db-f1-micro") -> bool:
        """Create a Cloud SQL instance."""
        logger.info(f"Creating Cloud SQL instance: {self.instance_name}")
        
        try:
            cmd = [
                'gcloud', 'sql', 'instances', 'create', self.instance_name,
                f'--project={self.project_id}',
                f'--region={region}',
                f'--database-version=POSTGRES_15',
                f'--tier={machine_type}',
                '--storage-type=SSD',
                '--storage-size=10GB',
                '--storage-auto-increase',
                '--backup',
                '--authorized-networks=0.0.0.0/0',  # Restrict this in production
                '--quiet'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to create Cloud SQL instance: {result.stderr}")
                return False
            
            logger.info(f"✓ Cloud SQL instance created: {self.instance_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create Cloud SQL instance: {e}")
            return False
    
    def create_database(self) -> bool:
        """Create the database in Cloud SQL instance."""
        logger.info(f"Creating database: {self.database_name}")
        
        try:
            cmd = [
                'gcloud', 'sql', 'databases', 'create', self.database_name,
                f'--instance={self.instance_name}',
                f'--project={self.project_id}',
                '--quiet'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to create database: {result.stderr}")
                return False
            
            logger.info(f"✓ Database created: {self.database_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create database: {e}")
            return False
    
    def create_user(self, username: str = "transparentsf", password: str = None) -> bool:
        """Create a database user."""
        if not password:
            password = os.urandom(16).hex()
        
        logger.info(f"Creating database user: {username}")
        
        try:
            cmd = [
                'gcloud', 'sql', 'users', 'create', username,
                f'--instance={self.instance_name}',
                f'--password={password}',
                f'--project={self.project_id}',
                '--quiet'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to create user: {result.stderr}")
                return False
            
            logger.info(f"✓ Database user created: {username}")
            logger.info(f"Password: {password}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            return False
    
    def import_database(self) -> bool:
        """Import the database from SQL file."""
        logger.info(f"Importing database from {self.backup_file}...")
        
        try:
            cmd = [
                'gcloud', 'sql', 'import', 'sql', self.instance_name,
                f'gs://{self.project_id}-backups/{self.backup_file}',
                f'--database={self.database_name}',
                f'--project={self.project_id}',
                '--quiet'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to import database: {result.stderr}")
                return False
            
            logger.info("✓ Database imported successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to import database: {e}")
            return False
    
    def upload_backup_to_gcs(self, bucket_name: str) -> bool:
        """Upload backup file to Google Cloud Storage."""
        logger.info(f"Uploading backup to GCS bucket: {bucket_name}")
        
        try:
            # Create bucket if it doesn't exist
            subprocess.run([
                'gsutil', 'mb', f'gs://{bucket_name}'
            ], capture_output=True)
            
            # Upload backup file
            cmd = [
                'gsutil', 'cp', self.backup_file, f'gs://{bucket_name}/'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to upload backup: {result.stderr}")
                return False
            
            logger.info("✓ Backup uploaded to GCS")
            return True
            
        except Exception as e:
            logger.error(f"Failed to upload backup: {e}")
            return False
    
    def get_connection_string(self, username: str = "transparentsf") -> str:
        """Get the Cloud SQL connection string."""
        return f"postgresql://{username}@/{self.database_name}?host=/cloudsql/{self.project_id}:{self.region}:{self.instance_name}"
    
    def print_connection_info(self, username: str = "transparentsf", password: str = None):
        """Print connection information for the application."""
        print("\n" + "="*60)
        print("CLOUD SQL CONNECTION INFORMATION")
        print("="*60)
        print(f"Project ID: {self.project_id}")
        print(f"Instance Name: {self.instance_name}")
        print(f"Database Name: {self.database_name}")
        print(f"Username: {username}")
        if password:
            print(f"Password: {password}")
        print()
        print("Connection String (for Cloud Run/App Engine):")
        print(f"postgresql://{username}@/{self.database_name}?host=/cloudsql/{self.project_id}:{self.region}:{self.instance_name}")
        print()
        print("Connection String (for external connections):")
        print(f"postgresql://{username}:{password}@{self.instance_name}:5432/{self.database_name}")
        print()
        print("Environment Variables for .env file:")
        print(f"DATABASE_URL=postgresql://{username}:{password}@{self.instance_name}:5432/{self.database_name}")
        print("="*60)


def main():
    """Main function to run the Cloud SQL migration."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate TransparentSF database to Google Cloud SQL')
    parser.add_argument('--project-id', required=True, help='Google Cloud project ID')
    parser.add_argument('--instance-name', required=True, help='Cloud SQL instance name')
    parser.add_argument('--database-name', default='transparentsf', help='Database name')
    parser.add_argument('--region', default='us-central1', help='GCP region')
    parser.add_argument('--machine-type', default='db-f1-micro', help='Machine type')
    parser.add_argument('--username', default='transparentsf', help='Database username')
    parser.add_argument('--password', help='Database password (generated if not provided)')
    parser.add_argument('--export-only', action='store_true', help='Only export database, do not create Cloud SQL')
    parser.add_argument('--import-only', action='store_true', help='Only import to existing Cloud SQL instance')
    
    args = parser.parse_args()
    
    # Initialize migrator
    migrator = CloudSQLMigrator(args.project_id, args.instance_name, args.database_name)
    
    # Check prerequisites
    if not migrator.check_prerequisites():
        logger.error("Prerequisites check failed")
        sys.exit(1)
    
    # Export database
    if not migrator.export_database():
        logger.error("Database export failed")
        sys.exit(1)
    
    if args.export_only:
        logger.info("Export completed. Use --import-only to import to Cloud SQL later.")
        return
    
    # Create Cloud SQL instance (if not import-only)
    if not args.import_only:
        if not migrator.create_cloud_sql_instance(args.region, args.machine_type):
            logger.error("Cloud SQL instance creation failed")
            sys.exit(1)
        
        # Create database
        if not migrator.create_database():
            logger.error("Database creation failed")
            sys.exit(1)
        
        # Create user
        if not migrator.create_user(args.username, args.password):
            logger.error("User creation failed")
            sys.exit(1)
    
    # Upload backup to GCS
    bucket_name = f"{args.project_id}-backups"
    if not migrator.upload_backup_to_gcs(bucket_name):
        logger.error("Backup upload failed")
        sys.exit(1)
    
    # Import database
    if not migrator.import_database():
        logger.error("Database import failed")
        sys.exit(1)
    
    # Print connection information
    migrator.print_connection_info(args.username, args.password)
    
    logger.info("Cloud SQL migration completed successfully!")


if __name__ == "__main__":
    main()
