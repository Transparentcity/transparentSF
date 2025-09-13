#!/usr/bin/env python3
"""
Setup script for Google Cloud Storage integration.

This script helps configure GCS for the TransparentSF project by:
1. Checking for required dependencies
2. Validating GCS configuration
3. Testing connectivity
4. Providing setup instructions
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

# Add the parent directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class GCSSetup:
    """Handles GCS setup and configuration validation."""
    
    def __init__(self):
        """Initialize the GCS setup."""
        self.script_dir = Path(__file__).parent.parent
        self.env_file = self.script_dir / '.env'
        self.example_env_file = self.script_dir / 'gcs_config_example.env'
        
    def check_dependencies(self) -> Dict[str, bool]:
        """
        Check if required dependencies are installed.
        
        Returns:
            Dictionary with dependency check results
        """
        logger.info("Checking dependencies...")
        
        dependencies = {
            'google-cloud-storage': False,
            'google-auth': False,
            'google-auth-oauthlib': False,
            'google-auth-httplib2': False,
            'python-dotenv': False
        }
        
        for dep in dependencies.keys():
            try:
                __import__(dep.replace('-', '_'))
                dependencies[dep] = True
                logger.info(f"✓ {dep} is installed")
            except ImportError:
                logger.warning(f"✗ {dep} is not installed")
        
        return dependencies
    
    def check_env_file(self) -> Dict[str, Any]:
        """
        Check environment file configuration.
        
        Returns:
            Dictionary with environment configuration status
        """
        logger.info("Checking environment configuration...")
        
        env_status = {
            'env_file_exists': self.env_file.exists(),
            'example_file_exists': self.example_env_file.exists(),
            'required_vars': {},
            'optional_vars': {}
        }
        
        if self.env_file.exists():
            # Load environment variables
            from dotenv import load_dotenv
            load_dotenv(self.env_file)
            
            # Check required variables
            required_vars = ['GCP_PROJECT_ID', 'GCS_BUCKET_NAME']
            for var in required_vars:
                value = os.getenv(var)
                env_status['required_vars'][var] = {
                    'exists': value is not None,
                    'value': value if value else None
                }
                if value:
                    logger.info(f"✓ {var} is set")
                else:
                    logger.warning(f"✗ {var} is not set")
            
            # Check optional variables
            optional_vars = ['GOOGLE_APPLICATION_CREDENTIALS', 'ENABLE_GCS']
            for var in optional_vars:
                value = os.getenv(var)
                env_status['optional_vars'][var] = {
                    'exists': value is not None,
                    'value': value if value else None
                }
                if value:
                    logger.info(f"✓ {var} is set: {value}")
                else:
                    logger.info(f"- {var} is not set (optional)")
        else:
            logger.warning("✗ .env file does not exist")
        
        return env_status
    
    def test_gcs_connection(self) -> Dict[str, Any]:
        """
        Test GCS connection and permissions.
        
        Returns:
            Dictionary with connection test results
        """
        logger.info("Testing GCS connection...")
        
        connection_test = {
            'gcs_available': False,
            'credentials_valid': False,
            'bucket_accessible': False,
            'bucket_exists': False,
            'can_write': False,
            'can_read': False,
            'error': None
        }
        
        try:
            # Import GCS modules
            from google.cloud import storage
            from google.auth.exceptions import DefaultCredentialsError
            
            # Test basic GCS availability
            connection_test['gcs_available'] = True
            logger.info("✓ Google Cloud Storage library is available")
            
            # Test credentials
            try:
                client = storage.Client()
                connection_test['credentials_valid'] = True
                logger.info("✓ Google Cloud credentials are valid")
            except DefaultCredentialsError as e:
                connection_test['error'] = f"Credentials error: {e}"
                logger.error(f"✗ Google Cloud credentials error: {e}")
                return connection_test
            
            # Test bucket access
            bucket_name = os.getenv('GCS_BUCKET_NAME')
            if not bucket_name:
                connection_test['error'] = "GCS_BUCKET_NAME not set"
                logger.error("✗ GCS_BUCKET_NAME not set")
                return connection_test
            
            bucket = client.bucket(bucket_name)
            
            # Check if bucket exists
            if bucket.exists():
                connection_test['bucket_exists'] = True
                connection_test['bucket_accessible'] = True
                logger.info(f"✓ Bucket '{bucket_name}' exists and is accessible")
                
                # Test write permissions
                try:
                    test_blob = bucket.blob('test/setup_test.txt')
                    test_content = f"GCS setup test - {os.getenv('USER', 'unknown')}"
                    test_blob.upload_from_string(test_content)
                    connection_test['can_write'] = True
                    logger.info("✓ Write permissions confirmed")
                    
                    # Test read permissions
                    downloaded_content = test_blob.download_as_text()
                    if downloaded_content == test_content:
                        connection_test['can_read'] = True
                        logger.info("✓ Read permissions confirmed")
                    
                    # Clean up test file
                    test_blob.delete()
                    logger.info("✓ Test file cleaned up")
                    
                except Exception as e:
                    connection_test['error'] = f"Permission test failed: {e}"
                    logger.error(f"✗ Permission test failed: {e}")
            else:
                connection_test['error'] = f"Bucket '{bucket_name}' does not exist"
                logger.error(f"✗ Bucket '{bucket_name}' does not exist")
            
        except ImportError as e:
            connection_test['error'] = f"Import error: {e}"
            logger.error(f"✗ GCS libraries not available: {e}")
        except Exception as e:
            connection_test['error'] = f"Unexpected error: {e}"
            logger.error(f"✗ Unexpected error: {e}")
        
        return connection_test
    
    def test_output_manager(self) -> Dict[str, Any]:
        """
        Test the output manager functionality.
        
        Returns:
            Dictionary with output manager test results
        """
        logger.info("Testing output manager...")
        
        test_results = {
            'output_manager_available': False,
            'storage_info': None,
            'health_check': None,
            'test_operations': {
                'store': False,
                'retrieve': False,
                'delete': False
            },
            'error': None
        }
        
        try:
            from tools.output_manager import get_output_manager
            
            test_results['output_manager_available'] = True
            logger.info("✓ Output manager is available")
            
            # Get storage info
            output_manager = get_output_manager()
            test_results['storage_info'] = output_manager.get_storage_info()
            logger.info(f"✓ Storage info retrieved: {test_results['storage_info']}")
            
            # Run health check
            test_results['health_check'] = output_manager.health_check()
            logger.info("✓ Health check completed")
            
            # Test basic operations
            test_content = f"Setup test content - {os.getenv('USER', 'unknown')}"
            test_filename = "setup_test.txt"
            
            # Test store
            if output_manager.store_notes(test_content, test_filename):
                test_results['test_operations']['store'] = True
                logger.info("✓ Store operation successful")
                
                # Test retrieve
                retrieved_content = output_manager.retrieve_notes(test_filename)
                if retrieved_content and test_content in retrieved_content:
                    test_results['test_operations']['retrieve'] = True
                    logger.info("✓ Retrieve operation successful")
                    
                    # Test delete
                    if output_manager.storage_manager.delete_file("notes", filename=test_filename):
                        test_results['test_operations']['delete'] = True
                        logger.info("✓ Delete operation successful")
                    else:
                        logger.warning("✗ Delete operation failed")
                else:
                    logger.warning("✗ Retrieve operation failed")
            else:
                logger.warning("✗ Store operation failed")
            
        except ImportError as e:
            test_results['error'] = f"Import error: {e}"
            logger.error(f"✗ Output manager not available: {e}")
        except Exception as e:
            test_results['error'] = f"Unexpected error: {e}"
            logger.error(f"✗ Output manager test failed: {e}")
        
        return test_results
    
    def create_env_file(self, project_id: str, bucket_name: str, 
                       credentials_path: Optional[str] = None) -> bool:
        """
        Create a .env file with the provided configuration.
        
        Args:
            project_id: Google Cloud project ID
            bucket_name: GCS bucket name
            credentials_path: Path to service account key file (optional)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            env_content = f"""# Google Cloud Storage Configuration
# Generated by setup_gcs.py

# Google Cloud Project ID
GCP_PROJECT_ID={project_id}

# Google Cloud Storage Bucket Name
GCS_BUCKET_NAME={bucket_name}

# Google Cloud Authentication
"""
            
            if credentials_path:
                env_content += f"GOOGLE_APPLICATION_CREDENTIALS={credentials_path}\n"
            else:
                env_content += "# GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/service-account-key.json\n"
            
            env_content += """
# Storage Configuration
ENABLE_GCS=true

# Local fallback directory (relative to ai/ directory)
LOCAL_OUTPUT_DIR=output

# Logging Configuration
LOG_LEVEL=INFO
"""
            
            with open(self.env_file, 'w') as f:
                f.write(env_content)
            
            logger.info(f"✓ Created .env file: {self.env_file}")
            return True
            
        except Exception as e:
            logger.error(f"✗ Failed to create .env file: {e}")
            return False
    
    def run_full_setup(self) -> Dict[str, Any]:
        """
        Run the complete setup process.
        
        Returns:
            Dictionary with complete setup results
        """
        logger.info("Running full GCS setup...")
        
        setup_results = {
            'dependencies': self.check_dependencies(),
            'env_config': self.check_env_file(),
            'gcs_connection': {},
            'output_manager': {},
            'setup_complete': False
        }
        
        # Only test GCS if dependencies are available
        if all(setup_results['dependencies'].values()):
            setup_results['gcs_connection'] = self.test_gcs_connection()
            setup_results['output_manager'] = self.test_output_manager()
            
            # Determine if setup is complete
            setup_results['setup_complete'] = (
                setup_results['env_config']['env_file_exists'] and
                all(var['exists'] for var in setup_results['env_config']['required_vars'].values()) and
                setup_results['gcs_connection'].get('can_write', False) and
                setup_results['output_manager'].get('test_operations', {}).get('store', False)
            )
        else:
            logger.error("✗ Cannot test GCS - missing dependencies")
        
        return setup_results
    
    def print_setup_instructions(self):
        """Print setup instructions for the user."""
        print("\n" + "="*60)
        print("GOOGLE CLOUD STORAGE SETUP INSTRUCTIONS")
        print("="*60)
        print()
        print("1. Install required dependencies:")
        print("   pip install google-cloud-storage google-auth google-auth-oauthlib google-auth-httplib2")
        print()
        print("2. Set up Google Cloud credentials:")
        print("   Option A - Service Account (recommended for production):")
        print("   - Create a service account in Google Cloud Console")
        print("   - Download the JSON key file")
        print("   - Set GOOGLE_APPLICATION_CREDENTIALS environment variable")
        print()
        print("   Option B - Default credentials (for development):")
        print("   - Run: gcloud auth application-default login")
        print()
        print("3. Create a GCS bucket:")
        print("   - Go to Google Cloud Console > Storage")
        print("   - Create a new bucket")
        print("   - Note the bucket name")
        print()
        print("4. Configure environment variables:")
        print("   - Copy gcs_config_example.env to .env")
        print("   - Fill in your GCP_PROJECT_ID and GCS_BUCKET_NAME")
        print("   - Set GOOGLE_APPLICATION_CREDENTIALS if using service account")
        print()
        print("5. Test the setup:")
        print("   python ai/tools/setup_gcs.py --test")
        print()
        print("6. Migrate existing files:")
        print("   python ai/tools/migrate_to_gcs.py --dry-run  # Test migration")
        print("   python ai/tools/migrate_to_gcs.py            # Run migration")
        print()
        print("="*60)


def main():
    """Main function to run the setup."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Setup Google Cloud Storage for TransparentSF')
    parser.add_argument('--test', action='store_true', help='Test current configuration')
    parser.add_argument('--create-env', action='store_true', help='Create .env file interactively')
    parser.add_argument('--instructions', action='store_true', help='Show setup instructions')
    
    args = parser.parse_args()
    
    setup = GCSSetup()
    
    if args.instructions:
        setup.print_setup_instructions()
        return
    
    if args.create_env:
        print("Creating .env file...")
        project_id = input("Enter your Google Cloud Project ID: ").strip()
        bucket_name = input("Enter your GCS bucket name: ").strip()
        credentials_path = input("Enter path to service account key file (optional): ").strip() or None
        
        if setup.create_env_file(project_id, bucket_name, credentials_path):
            print("✓ .env file created successfully!")
        else:
            print("✗ Failed to create .env file")
            sys.exit(1)
    
    if args.test:
        results = setup.run_full_setup()
        
        print("\n" + "="*60)
        print("SETUP TEST RESULTS")
        print("="*60)
        
        # Dependencies
        print("\nDependencies:")
        for dep, status in results['dependencies'].items():
            print(f"  {'✓' if status else '✗'} {dep}")
        
        # Environment
        print("\nEnvironment Configuration:")
        print(f"  {'✓' if results['env_config']['env_file_exists'] else '✗'} .env file exists")
        for var, info in results['env_config']['required_vars'].items():
            print(f"  {'✓' if info['exists'] else '✗'} {var}")
        
        # GCS Connection
        if results['gcs_connection']:
            print("\nGCS Connection:")
            print(f"  {'✓' if results['gcs_connection']['gcs_available'] else '✗'} GCS library available")
            print(f"  {'✓' if results['gcs_connection']['credentials_valid'] else '✗'} Credentials valid")
            print(f"  {'✓' if results['gcs_connection']['bucket_accessible'] else '✗'} Bucket accessible")
            print(f"  {'✓' if results['gcs_connection']['can_write'] else '✗'} Write permissions")
            print(f"  {'✓' if results['gcs_connection']['can_read'] else '✗'} Read permissions")
        
        # Output Manager
        if results['output_manager']:
            print("\nOutput Manager:")
            print(f"  {'✓' if results['output_manager']['output_manager_available'] else '✗'} Output manager available")
            if results['output_manager']['test_operations']:
                ops = results['output_manager']['test_operations']
                print(f"  {'✓' if ops['store'] else '✗'} Store operation")
                print(f"  {'✓' if ops['retrieve'] else '✗'} Retrieve operation")
                print(f"  {'✓' if ops['delete'] else '✗'} Delete operation")
        
        # Overall status
        print(f"\nOverall Status: {'✓ SETUP COMPLETE' if results['setup_complete'] else '✗ SETUP INCOMPLETE'}")
        
        if not results['setup_complete']:
            print("\nRun with --instructions to see setup instructions")
            sys.exit(1)
    else:
        # Default: show instructions
        setup.print_setup_instructions()


if __name__ == "__main__":
    main()
