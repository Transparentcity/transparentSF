#!/usr/bin/env python3
"""
Google Compute Engine setup script for TransparentSF.

This script helps set up a Compute Engine VM instance for hosting the TransparentSF application,
including VM creation, firewall configuration, and application deployment.
"""

import os
import sys
import subprocess
import logging
import json
from datetime import datetime
from typing import Dict, Any, Optional

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('compute_engine_setup.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ComputeEngineSetup:
    """Handles setup of Compute Engine VM for TransparentSF."""
    
    def __init__(self, project_id: str, instance_name: str, zone: str = "us-central1-a"):
        """
        Initialize the Compute Engine setup.
        
        Args:
            project_id: Google Cloud project ID
            instance_name: VM instance name
            zone: GCP zone
        """
        self.project_id = project_id
        self.instance_name = instance_name
        self.zone = zone
        self.machine_type = "e2-medium"  # 2 vCPUs, 4 GB RAM
        self.image_family = "ubuntu-2204-lts"
        self.image_project = "ubuntu-os-cloud"
        
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
        
        return True
    
    def enable_apis(self) -> bool:
        """Enable required Google Cloud APIs."""
        logger.info("Enabling required APIs...")
        
        apis = [
            "compute.googleapis.com",
            "sqladmin.googleapis.com",
            "storage-api.googleapis.com",
            "cloudresourcemanager.googleapis.com"
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
        
        return True
    
    def create_firewall_rules(self) -> bool:
        """Create firewall rules for the application."""
        logger.info("Creating firewall rules...")
        
        # Allow HTTP traffic
        try:
            cmd = [
                'gcloud', 'compute', 'firewall-rules', 'create', 'allow-http',
                f'--project={self.project_id}',
                '--allow=tcp:80',
                '--source-ranges=0.0.0.0/0',
                '--target-tags=http-server',
                '--quiet'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0 and "already exists" not in result.stderr:
                logger.warning(f"Failed to create HTTP firewall rule: {result.stderr}")
            else:
                logger.info("✓ HTTP firewall rule created/exists")
                
        except Exception as e:
            logger.warning(f"Failed to create HTTP firewall rule: {e}")
        
        # Allow HTTPS traffic
        try:
            cmd = [
                'gcloud', 'compute', 'firewall-rules', 'create', 'allow-https',
                f'--project={self.project_id}',
                '--allow=tcp:443',
                '--source-ranges=0.0.0.0/0',
                '--target-tags=https-server',
                '--quiet'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0 and "already exists" not in result.stderr:
                logger.warning(f"Failed to create HTTPS firewall rule: {result.stderr}")
            else:
                logger.info("✓ HTTPS firewall rule created/exists")
                
        except Exception as e:
            logger.warning(f"Failed to create HTTPS firewall rule: {e}")
        
        # Allow application port (8000)
        try:
            cmd = [
                'gcloud', 'compute', 'firewall-rules', 'create', 'allow-transparentsf',
                f'--project={self.project_id}',
                '--allow=tcp:8000',
                '--source-ranges=0.0.0.0/0',
                '--target-tags=transparentsf-server',
                '--quiet'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0 and "already exists" not in result.stderr:
                logger.warning(f"Failed to create TransparentSF firewall rule: {result.stderr}")
            else:
                logger.info("✓ TransparentSF firewall rule created/exists")
                
        except Exception as e:
            logger.warning(f"Failed to create TransparentSF firewall rule: {e}")
        
        return True
    
    def create_vm_instance(self) -> bool:
        """Create the VM instance."""
        logger.info(f"Creating VM instance: {self.instance_name}")
        
        try:
            cmd = [
                'gcloud', 'compute', 'instances', 'create', self.instance_name,
                f'--project={self.project_id}',
                f'--zone={self.zone}',
                f'--machine-type={self.machine_type}',
                f'--image-family={self.image_family}',
                f'--image-project={self.image_project}',
                '--boot-disk-size=20GB',
                '--boot-disk-type=pd-standard',
                '--tags=http-server,https-server,transparentsf-server',
                '--metadata-from-file=startup-script=startup_script.sh',
                '--quiet'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to create VM instance: {result.stderr}")
                return False
            
            logger.info(f"✓ VM instance created: {self.instance_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create VM instance: {e}")
            return False
    
    def create_startup_script(self) -> str:
        """Create the startup script for the VM."""
        startup_script = '''#!/bin/bash

# Update system
apt-get update
apt-get upgrade -y

# Install Python 3.11 and pip
apt-get install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Install PostgreSQL client
apt-get install -y postgresql-client

# Install other dependencies
apt-get install -y git curl wget unzip

# Install Google Cloud SDK
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key --keyring /usr/share/keyrings/cloud.google.gpg add -
apt-get update
apt-get install -y google-cloud-sdk

# Create application user
useradd -m -s /bin/bash transparentsf
usermod -aG sudo transparentsf

# Create application directory
mkdir -p /opt/transparentsf
chown transparentsf:transparentsf /opt/transparentsf

# Switch to application user
sudo -u transparentsf bash << 'EOF'
cd /opt/transparentsf

# Clone the repository (you'll need to update this with your actual repo)
git clone https://github.com/Transparentcity/transparentSF.git .

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
cd ai
pip install --upgrade pip
pip install -r requirements.txt

# Create systemd service
sudo tee /etc/systemd/system/transparentsf.service > /dev/null << 'SERVICE_EOF'
[Unit]
Description=TransparentSF Application
After=network.target

[Service]
Type=simple
User=transparentsf
WorkingDirectory=/opt/transparentsf/ai
Environment=PATH=/opt/transparentsf/venv/bin
ExecStart=/opt/transparentsf/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable transparentsf
sudo systemctl start transparentsf

# Install and configure Nginx
sudo apt-get install -y nginx

# Create Nginx configuration
sudo tee /etc/nginx/sites-available/transparentsf > /dev/null << 'NGINX_EOF'
server {
    listen 80;
    server_name _;

    # Increase client body size for file uploads
    client_max_body_size 50M;

    # Timeout settings
    proxy_connect_timeout 300s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
    send_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Additional timeout settings for this location
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        
        # Buffer settings for better performance
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_busy_buffers_size 8k;
    }

    # Static files
    location /static/ {
        alias /opt/transparentsf/ai/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # API routes with extended timeouts
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Extended timeouts for API calls that might take longer
        proxy_connect_timeout 600s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        
        # Buffer settings
        proxy_buffering on;
        proxy_buffer_size 8k;
        proxy_buffers 16 8k;
        proxy_busy_buffers_size 16k;
    }

    # Backend routes with extended timeouts
    location /backend/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Extended timeouts for backend operations
        proxy_connect_timeout 600s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        
        # Buffer settings
        proxy_buffering on;
        proxy_buffer_size 8k;
        proxy_buffers 16 8k;
        proxy_busy_buffers_size 16k;
    }
}
NGINX_EOF

# Enable the site
sudo ln -sf /etc/nginx/sites-available/transparentsf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl restart nginx

EOF

# Set up log rotation
tee /etc/logrotate.d/transparentsf > /dev/null << 'LOGROTATE_EOF'
/opt/transparentsf/ai/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 transparentsf transparentsf
    postrotate
        systemctl reload transparentsf
    endscript
}
LOGROTATE_EOF

echo "TransparentSF setup completed successfully!"
'''
        
        return startup_script
    
    def save_startup_script(self) -> bool:
        """Save the startup script to a file."""
        try:
            script_content = self.create_startup_script()
            
            with open('startup_script.sh', 'w') as f:
                f.write(script_content)
            
            logger.info("✓ Startup script created: startup_script.sh")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create startup script: {e}")
            return False
    
    def get_vm_info(self) -> Dict[str, Any]:
        """Get information about the VM instance."""
        try:
            cmd = [
                'gcloud', 'compute', 'instances', 'describe', self.instance_name,
                f'--project={self.project_id}',
                f'--zone={self.zone}',
                '--format=json'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to get VM info: {result.stderr}")
                return {}
            
            return json.loads(result.stdout)
            
        except Exception as e:
            logger.error(f"Failed to get VM info: {e}")
            return {}
    
    def print_connection_info(self):
        """Print connection information for the VM."""
        vm_info = self.get_vm_info()
        
        if not vm_info:
            logger.error("Could not retrieve VM information")
            return
        
        external_ip = None
        for interface in vm_info.get('networkInterfaces', []):
            if 'accessConfigs' in interface:
                for config in interface['accessConfigs']:
                    if config.get('type') == 'ONE_TO_ONE_NAT':
                        external_ip = config.get('natIP')
                        break
        
        print("\n" + "="*60)
        print("COMPUTE ENGINE VM INFORMATION")
        print("="*60)
        print(f"Project ID: {self.project_id}")
        print(f"Instance Name: {self.instance_name}")
        print(f"Zone: {self.zone}")
        print(f"Machine Type: {self.machine_type}")
        if external_ip:
            print(f"External IP: {external_ip}")
            print(f"Application URL: http://{external_ip}")
        print()
        print("SSH Connection:")
        print(f"gcloud compute ssh {self.instance_name} --project={self.project_id} --zone={self.zone}")
        print()
        print("To check application status:")
        print(f"gcloud compute ssh {self.instance_name} --project={self.project_id} --zone={self.zone} --command='sudo systemctl status transparentsf'")
        print()
        print("To view application logs:")
        print(f"gcloud compute ssh {self.instance_name} --project={self.project_id} --zone={self.zone} --command='sudo journalctl -u transparentsf -f'")
        print("="*60)


def main():
    """Main function to run the Compute Engine setup."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Set up Compute Engine VM for TransparentSF')
    parser.add_argument('--project-id', required=True, help='Google Cloud project ID')
    parser.add_argument('--instance-name', required=True, help='VM instance name')
    parser.add_argument('--zone', default='us-central1-a', help='GCP zone')
    parser.add_argument('--machine-type', default='e2-medium', help='Machine type')
    parser.add_argument('--enable-apis-only', action='store_true', help='Only enable required APIs')
    parser.add_argument('--create-firewall-only', action='store_true', help='Only create firewall rules')
    
    args = parser.parse_args()
    
    # Initialize setup
    setup = ComputeEngineSetup(args.project_id, args.instance_name, args.zone)
    setup.machine_type = args.machine_type
    
    # Check prerequisites
    if not setup.check_prerequisites():
        logger.error("Prerequisites check failed")
        sys.exit(1)
    
    # Enable APIs
    if not setup.enable_apis():
        logger.error("API enablement failed")
        sys.exit(1)
    
    if args.enable_apis_only:
        logger.info("APIs enabled successfully")
        return
    
    # Create firewall rules
    if not setup.create_firewall_rules():
        logger.error("Firewall rules creation failed")
        sys.exit(1)
    
    if args.create_firewall_only:
        logger.info("Firewall rules created successfully")
        return
    
    # Create startup script
    if not setup.save_startup_script():
        logger.error("Startup script creation failed")
        sys.exit(1)
    
    # Create VM instance
    if not setup.create_vm_instance():
        logger.error("VM instance creation failed")
        sys.exit(1)
    
    # Print connection information
    setup.print_connection_info()
    
    logger.info("Compute Engine setup completed successfully!")
    logger.info("The VM is now starting up. It may take a few minutes to be ready.")


if __name__ == "__main__":
    main()

