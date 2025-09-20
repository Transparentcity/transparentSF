#!/bin/bash

# TransparentSF GCP Deployment Script
# This script commits changes and deploys them to the Google Compute VM

set -e  # Exit on any error

# Configuration from DEPLOYMENT.md
PROJECT_ID="euphoric-oath-467718-p3"
VM_NAME="transparentsf-prod"
ZONE="us-central1-a"
VM_PATH="/opt/transparentsf"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Starting TransparentSF GCP Deployment...${NC}"

# Function to print status messages
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Check if we're in the right directory
if [ ! -f "requirements.txt" ] || [ ! -d "ai" ]; then
    print_error "Not in TransparentSF root directory. Please run from the project root."
    exit 1
fi

# Check if gcloud is installed and authenticated
if ! command -v gcloud &> /dev/null; then
    print_error "gcloud CLI is not installed. Please install it first:"
    echo "https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if authenticated
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | head -1 > /dev/null; then
    print_error "Not authenticated with gcloud. Please run: gcloud auth login"
    exit 1
fi

# Set the project
print_status "Setting GCP project to $PROJECT_ID"
gcloud config set project $PROJECT_ID

# Check if VM exists
print_status "Checking if VM $VM_NAME exists..."
if ! gcloud compute instances describe $VM_NAME --zone=$ZONE &>/dev/null; then
    print_error "VM $VM_NAME not found in zone $ZONE"
    print_warning "Please check the VM name and zone in the deployment configuration"
    exit 1
fi

print_status "VM found and accessible"

# Check git status
print_status "Checking git status..."
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${YELLOW}📝 Found uncommitted changes:${NC}"
    git status --short
    
    echo ""
    read -p "Do you want to commit these changes? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo ""
        read -p "Enter commit message: " commit_message
        if [ -z "$commit_message" ]; then
            commit_message="Deploy updates to GCP VM $(date '+%Y-%m-%d %H:%M')"
        fi
        
        print_status "Adding all changes to git..."
        git add .
        
        print_status "Committing changes..."
        git commit -m "$commit_message"
        
        print_status "Pushing to remote repository..."
        git push origin $(git branch --show-current)
    else
        print_warning "Skipping commit. Only committed changes will be deployed."
    fi
else
    print_status "No uncommitted changes found"
fi

# Get current branch
CURRENT_BRANCH=$(git branch --show-current)
print_status "Current branch: $CURRENT_BRANCH"

# Deploy to VM
print_status "Connecting to VM and deploying..."

# Create deployment commands
DEPLOY_COMMANDS="
set -e
cd $VM_PATH

echo '🔄 Pulling latest changes...'
sudo -u transparentsf git fetch origin
sudo -u transparentsf git checkout $CURRENT_BRANCH
sudo -u transparentsf git pull origin $CURRENT_BRANCH

echo '📦 Installing/updating Python dependencies...'
sudo -u transparentsf /opt/transparentsf/venv/bin/pip install -r requirements.txt

echo '🔧 Setting up directories and permissions...'
sudo -u transparentsf mkdir -p ai/logs ai/output ai/static
sudo chown -R transparentsf:transparentsf ai/logs ai/output ai/static
sudo chmod 755 ai/logs ai/output ai/static

echo '🐳 Checking Qdrant service...'
if ! systemctl is-active --quiet qdrant; then
    echo 'Starting Qdrant service...'
    sudo systemctl start qdrant
fi

echo '🔄 Restarting TransparentSF application...'
sudo systemctl restart transparentsf

echo '⏳ Waiting for application to start...'
sleep 5

echo '✅ Checking application status...'
sudo systemctl status transparentsf --no-pager -l

echo '🌐 Application should be available at: http://$(curl -s metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip -H \"Metadata-Flavor: Google\")'
"

# Execute deployment on VM
print_status "Executing deployment commands on VM..."
gcloud compute ssh $VM_NAME \
    --project=$PROJECT_ID \
    --zone=$ZONE \
    --command="$DEPLOY_COMMANDS"

if [ $? -eq 0 ]; then
    print_status "Deployment completed successfully!"
    
    # Get VM external IP
    EXTERNAL_IP=$(gcloud compute instances describe $VM_NAME \
        --project=$PROJECT_ID \
        --zone=$ZONE \
        --format="get(networkInterfaces[0].accessConfigs[0].natIP)")
    
    echo ""
    echo -e "${GREEN}🎉 Deployment Complete!${NC}"
    echo -e "${BLUE}📍 Your application is available at: http://$EXTERNAL_IP${NC}"
    echo ""
    echo -e "${YELLOW}📋 Post-deployment checklist:${NC}"
    echo "   • Test the application in your browser"
    echo "   • Check logs if needed: sudo journalctl -u transparentsf -f"
    echo "   • Monitor system resources: htop"
    echo ""
    
    # Optional: Open application in browser
    read -p "Would you like to open the application in your browser? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        open "http://$EXTERNAL_IP" 2>/dev/null || echo "Please manually open: http://$EXTERNAL_IP"
    fi
    
else
    print_error "Deployment failed. Check the error messages above."
    echo ""
    echo -e "${YELLOW}🔍 Troubleshooting steps:${NC}"
    echo "   1. Check VM logs: gcloud compute ssh $VM_NAME --project=$PROJECT_ID --zone=$ZONE --command='sudo journalctl -u transparentsf -n 50'"
    echo "   2. Check application status: gcloud compute ssh $VM_NAME --project=$PROJECT_ID --zone=$ZONE --command='sudo systemctl status transparentsf'"
    echo "   3. Check disk space: gcloud compute ssh $VM_NAME --project=$PROJECT_ID --zone=$ZONE --command='df -h'"
    exit 1
fi
