#!/usr/bin/env python3
"""
Complete Google Cloud Platform migration script for TransparentSF.

This script orchestrates the entire migration process:
1. Database migration to Cloud SQL
2. File storage migration to GCS
3. Compute Engine VM setup
4. Application deployment
5. Monitoring and security configuration
"""

import os
import sys
import subprocess
import logging
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

# Add the parent directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.cloud_sql_migration import CloudSQLMigrator
from tools.compute_engine_setup import ComputeEngineSetup
from tools.migrate_to_gcs import OutputMigrator

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('gcp_migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class GCPMigrationOrchestrator:
    """Orchestrates the complete migration to Google Cloud Platform."""
    
    def __init__(self, project_id: str, region: str = "us-central1"):
        """
        Initialize the migration orchestrator.
        
        Args:
            project_id: Google Cloud project ID
            region: GCP region
        """
        self.project_id = project_id
        self.region = region
        self.zone = f"{region}-a"
        
        # Resource names
        self.sql_instance_name = f"transparentsf-db-{datetime.now().strftime('%Y%m%d')}"
        self.vm_instance_name = f"transparentsf-app-{datetime.now().strftime('%Y%m%d')}"
        self.gcs_bucket_name = f"{project_id}-transparentsf-storage"
        self.database_name = "transparentsf"
        self.database_user = "transparentsf"
        
        # Migration status
        self.migration_status = {
            "apis_enabled": False,
            "cloud_sql_created": False,
            "database_migrated": False,
            "gcs_configured": False,
            "files_migrated": False,
            "vm_created": False,
            "application_deployed": False,
            "monitoring_configured": False
        }
        
    def check_prerequisites(self) -> bool:
        """Check if all prerequisites are met."""
        logger.info("Checking migration prerequisites...")
        
        # Check gcloud CLI
        try:
            result = subprocess.run(['gcloud', '--version'], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("gcloud CLI not found. Please install Google Cloud SDK.")
                return False
            logger.info("✓ gcloud CLI found")
        except FileNotFoundError:
            logger.error("gcloud CLI not found. Please install Google Cloud SDK.")
            return False
        
        # Check authentication
        try:
            result = subprocess.run(['gcloud', 'auth', 'list', '--filter=status:ACTIVE'], 
                                  capture_output=True, text=True)
            if result.returncode != 0 or not result.stdout.strip():
                logger.error("No active gcloud authentication found. Please run 'gcloud auth login'")
                return False
            logger.info("✓ gcloud authentication active")
        except Exception as e:
            logger.error(f"Failed to check gcloud authentication: {e}")
            return False
        
        # Check project
        try:
            result = subprocess.run(['gcloud', 'config', 'get-value', 'project'], 
                                  capture_output=True, text=True)
            if result.returncode != 0 or not result.stdout.strip():
                logger.error("No project set in gcloud. Please run 'gcloud config set project PROJECT_ID'")
                return False
            logger.info(f"✓ gcloud project set: {result.stdout.strip()}")
        except Exception as e:
            logger.error(f"Failed to check gcloud project: {e}")
            return False
        
        return True
    
    def enable_apis(self) -> bool:
        """Enable all required Google Cloud APIs."""
        logger.info("Enabling required Google Cloud APIs...")
        
        apis = [
            "compute.googleapis.com",
            "sqladmin.googleapis.com",
            "storage-api.googleapis.com",
            "cloudresourcemanager.googleapis.com",
            "monitoring.googleapis.com",
            "logging.googleapis.com",
            "cloudtrace.googleapis.com",
            "cloudprofiler.googleapis.com"
        ]
        
        for api in apis:
            try:
                cmd = [
                    'gcloud', 'services', 'enable', api,
                    f'--project={self.project_id}',
                    '--quiet'
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode != 0:
                    logger.warning(f"Failed to enable {api}: {result.stderr}")
                else:
                    logger.info(f"✓ Enabled {api}")
                    
            except Exception as e:
                logger.warning(f"Failed to enable {api}: {e}")
        
        self.migration_status["apis_enabled"] = True
        return True
    
    def migrate_database(self) -> bool:
        """Migrate database to Cloud SQL."""
        logger.info("Starting database migration to Cloud SQL...")
        
        try:
            # Initialize Cloud SQL migrator
            sql_migrator = CloudSQLMigrator(
                self.project_id, 
                self.sql_instance_name, 
                self.database_name
            )
            
            # Check prerequisites
            if not sql_migrator.check_prerequisites():
                logger.error("Database migration prerequisites failed")
                return False
            
            # Export current database
            if not sql_migrator.export_database():
                logger.error("Database export failed")
                return False
            
            # Create Cloud SQL instance
            if not sql_migrator.create_cloud_sql_instance(self.region):
                logger.error("Cloud SQL instance creation failed")
                return False
            
            # Create database
            if not sql_migrator.create_database():
                logger.error("Database creation failed")
                return False
            
            # Create user
            if not sql_migrator.create_user(self.database_user):
                logger.error("User creation failed")
                return False
            
            # Upload backup to GCS
            bucket_name = f"{self.project_id}-backups"
            if not sql_migrator.upload_backup_to_gcs(bucket_name):
                logger.error("Backup upload failed")
                return False
            
            # Import database
            if not sql_migrator.import_database():
                logger.error("Database import failed")
                return False
            
            self.migration_status["cloud_sql_created"] = True
            self.migration_status["database_migrated"] = True
            
            logger.info("✓ Database migration completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Database migration failed: {e}")
            return False
    
    def setup_gcs_storage(self) -> bool:
        """Set up Google Cloud Storage for file storage."""
        logger.info("Setting up Google Cloud Storage...")
        
        try:
            # Create GCS bucket
            cmd = [
                'gsutil', 'mb', f'gs://{self.gcs_bucket_name}',
                '-p', self.project_id,
                '-c', 'STANDARD',
                '-l', self.region
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0 and "already exists" not in result.stderr:
                logger.error(f"Failed to create GCS bucket: {result.stderr}")
                return False
            
            logger.info(f"✓ GCS bucket created/exists: {self.gcs_bucket_name}")
            
            # Set bucket permissions
            cmd = [
                'gsutil', 'iam', 'ch', 
                f'serviceAccount:{self.project_id}@appspot.gserviceaccount.com:objectAdmin',
                f'gs://{self.gcs_bucket_name}'
            ]
            
            subprocess.run(cmd, capture_output=True, text=True)
            
            self.migration_status["gcs_configured"] = True
            return True
            
        except Exception as e:
            logger.error(f"GCS setup failed: {e}")
            return False
    
    def migrate_files_to_gcs(self) -> bool:
        """Migrate existing files to Google Cloud Storage."""
        logger.info("Migrating files to Google Cloud Storage...")
        
        try:
            # Set environment variables for GCS
            os.environ['GCP_PROJECT_ID'] = self.project_id
            os.environ['GCS_BUCKET_NAME'] = self.gcs_bucket_name
            os.environ['ENABLE_GCS'] = 'true'
            
            # Initialize file migrator
            file_migrator = OutputMigrator()
            
            # Run migration
            stats = file_migrator.migrate_all_files(dry_run=False)
            
            if stats['failed_uploads'] > 0:
                logger.warning(f"File migration completed with {stats['failed_uploads']} failures")
            else:
                logger.info("✓ File migration completed successfully")
            
            self.migration_status["files_migrated"] = True
            return True
            
        except Exception as e:
            logger.error(f"File migration failed: {e}")
            return False
    
    def setup_compute_engine(self) -> bool:
        """Set up Compute Engine VM for the application."""
        logger.info("Setting up Compute Engine VM...")
        
        try:
            # Initialize Compute Engine setup
            vm_setup = ComputeEngineSetup(
                self.project_id, 
                self.vm_instance_name, 
                self.zone
            )
            
            # Create firewall rules
            if not vm_setup.create_firewall_rules():
                logger.error("Firewall rules creation failed")
                return False
            
            # Create startup script
            if not vm_setup.save_startup_script():
                logger.error("Startup script creation failed")
                return False
            
            # Create VM instance
            if not vm_setup.create_vm_instance():
                logger.error("VM instance creation failed")
                return False
            
            self.migration_status["vm_created"] = True
            logger.info("✓ Compute Engine VM created successfully")
            return True
            
        except Exception as e:
            logger.error(f"Compute Engine setup failed: {e}")
            return False
    
    def deploy_application(self) -> bool:
        """Deploy the application to the VM."""
        logger.info("Deploying application to VM...")
        
        try:
            # Wait for VM to be ready
            logger.info("Waiting for VM to be ready...")
            time.sleep(60)  # Wait 1 minute for VM to start
            
            # Copy application files to VM
            logger.info("Copying application files to VM...")
            
            # Create deployment script
            deployment_script = f'''#!/bin/bash
# Update application configuration for Cloud SQL
cd /opt/transparentsf

# Create .env file with Cloud SQL connection
cat > ai/.env << 'EOF'
# Database Configuration
DATABASE_URL=postgresql://{self.database_user}@/{self.database_name}?host=/cloudsql/{self.project_id}:{self.region}:{self.sql_instance_name}

# Google Cloud Storage Configuration
GCP_PROJECT_ID={self.project_id}
GCS_BUCKET_NAME={self.gcs_bucket_name}
ENABLE_GCS=true

# Application Configuration
LOG_LEVEL=INFO
EOF

# Restart the application
sudo systemctl restart transparentsf

# Check application status
sudo systemctl status transparentsf
'''
            
            # Save deployment script
            with open('deploy_app.sh', 'w') as f:
                f.write(deployment_script)
            
            # Copy and execute deployment script
            cmd = [
                'gcloud', 'compute', 'scp', 'deploy_app.sh',
                f'{self.vm_instance_name}:~/deploy_app.sh',
                f'--project={self.project_id}',
                f'--zone={self.zone}'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to copy deployment script: {result.stderr}")
                return False
            
            # Execute deployment script
            cmd = [
                'gcloud', 'compute', 'ssh', self.vm_instance_name,
                f'--project={self.project_id}',
                f'--zone={self.zone}',
                '--command=chmod +x ~/deploy_app.sh && ~/deploy_app.sh'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to execute deployment script: {result.stderr}")
                return False
            
            self.migration_status["application_deployed"] = True
            logger.info("✓ Application deployed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Application deployment failed: {e}")
            return False
    
    def setup_monitoring(self) -> bool:
        """Set up monitoring and logging."""
        logger.info("Setting up monitoring and logging...")
        
        try:
            # Enable monitoring agent on VM
            cmd = [
                'gcloud', 'compute', 'ssh', self.vm_instance_name,
                f'--project={self.project_id}',
                f'--zone={self.zone}',
                '--command=sudo apt-get update && sudo apt-get install -y google-cloud-ops-agent'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.warning(f"Failed to install monitoring agent: {result.stderr}")
            else:
                logger.info("✓ Monitoring agent installed")
            
            self.migration_status["monitoring_configured"] = True
            return True
            
        except Exception as e:
            logger.warning(f"Monitoring setup failed: {e}")
            return True  # Don't fail the entire migration for monitoring issues
    
    def print_migration_summary(self):
        """Print a summary of the migration."""
        vm_info = self.get_vm_info()
        external_ip = None
        
        if vm_info:
            for interface in vm_info.get('networkInterfaces', []):
                if 'accessConfigs' in interface:
                    for config in interface['accessConfigs']:
                        if config.get('type') == 'ONE_TO_ONE_NAT':
                            external_ip = config.get('natIP')
                            break
        
        print("\n" + "="*80)
        print("TRANSPARENTSF GCP MIGRATION SUMMARY")
        print("="*80)
        print(f"Project ID: {self.project_id}")
        print(f"Region: {self.region}")
        print(f"Zone: {self.zone}")
        print()
        print("Resources Created:")
        print(f"  • Cloud SQL Instance: {self.sql_instance_name}")
        print(f"  • VM Instance: {self.vm_instance_name}")
        print(f"  • GCS Bucket: {self.gcs_bucket_name}")
        print()
        print("Migration Status:")
        for status, completed in self.migration_status.items():
            status_icon = "✓" if completed else "✗"
            print(f"  {status_icon} {status.replace('_', ' ').title()}")
        print()
        if external_ip:
            print("Application Access:")
            print(f"  • URL: http://{external_ip}")
            print(f"  • SSH: gcloud compute ssh {self.vm_instance_name} --project={self.project_id} --zone={self.zone}")
        print()
        print("Next Steps:")
        print("  1. Update your DNS to point to the VM's external IP")
        print("  2. Set up SSL certificate (Let's Encrypt recommended)")
        print("  3. Configure custom domain")
        print("  4. Set up automated backups")
        print("  5. Monitor application performance")
        print("="*80)
    
    def get_vm_info(self) -> Optional[Dict[str, Any]]:
        """Get VM information."""
        try:
            cmd = [
                'gcloud', 'compute', 'instances', 'describe', self.vm_instance_name,
                f'--project={self.project_id}',
                f'--zone={self.zone}',
                '--format=json'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                return None
            
            return json.loads(result.stdout)
            
        except Exception:
            return None
    
    def run_migration(self, skip_database: bool = False, skip_files: bool = False) -> bool:
        """Run the complete migration process."""
        logger.info("Starting TransparentSF GCP migration...")
        
        try:
            # Check prerequisites
            if not self.check_prerequisites():
                logger.error("Prerequisites check failed")
                return False
            
            # Enable APIs
            if not self.enable_apis():
                logger.error("API enablement failed")
                return False
            
            # Migrate database
            if not skip_database:
                if not self.migrate_database():
                    logger.error("Database migration failed")
                    return False
            
            # Set up GCS
            if not self.setup_gcs_storage():
                logger.error("GCS setup failed")
                return False
            
            # Migrate files
            if not skip_files:
                if not self.migrate_files_to_gcs():
                    logger.error("File migration failed")
                    return False
            
            # Set up Compute Engine
            if not self.setup_compute_engine():
                logger.error("Compute Engine setup failed")
                return False
            
            # Deploy application
            if not self.deploy_application():
                logger.error("Application deployment failed")
                return False
            
            # Set up monitoring
            self.setup_monitoring()
            
            # Print summary
            self.print_migration_summary()
            
            logger.info("✓ GCP migration completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False


def main():
    """Main function to run the GCP migration."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate TransparentSF to Google Cloud Platform')
    parser.add_argument('--project-id', required=True, help='Google Cloud project ID')
    parser.add_argument('--region', default='us-central1', help='GCP region')
    parser.add_argument('--skip-database', action='store_true', help='Skip database migration')
    parser.add_argument('--skip-files', action='store_true', help='Skip file migration')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run (not implemented)')
    
    args = parser.parse_args()
    
    # Initialize migration orchestrator
    orchestrator = GCPMigrationOrchestrator(args.project_id, args.region)
    
    # Run migration
    success = orchestrator.run_migration(
        skip_database=args.skip_database,
        skip_files=args.skip_files
    )
    
    if success:
        logger.info("Migration completed successfully!")
        sys.exit(0)
    else:
        logger.error("Migration failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

