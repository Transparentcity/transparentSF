# Qdrant Vector Database Production Fix

## Problem
The vector database reload was failing in production because all vector loader scripts were hardcoded to connect to `localhost:6333`, but Qdrant was not properly configured to run in the production environment.

## Solution
This fix implements proper environment-based configuration for Qdrant connections and sets up Docker-based Qdrant deployment for production.

## Changes Made

### 1. Environment Configuration
- **File**: `ai/gcp_env_example.env`
- **Added**: `QDRANT_URL=localhost` and `QDRANT_PORT=6333` environment variables

### 2. Updated Vector Loader Scripts
All vector loader scripts now use environment variables for Qdrant connection:
- `ai/vector_loader_sfpublic.py`
- `ai/vector_loader_periodic.py`
- `ai/prep_data.py`
- `ai/tools/vector_query.py`
- `ai/generate_dashboard_metrics.py`
- `ai/backend.py`

### 3. Docker Configuration
- **File**: `docker-compose.yml` - Added Qdrant service configuration
- **File**: `ai/tools/production/startup_script.sh` - Updated to install Docker and start Qdrant

### 4. Production Setup
- Added Docker installation to startup script
- Created systemd service for Qdrant (`qdrant.service`)
- Added health checks and proper startup sequence

### 5. Management Tools
- **File**: `ai/tools/qdrant_manager.py` - Utility script for Qdrant management
- **File**: `DEPLOYMENT.md` - Updated troubleshooting section

## Deployment Instructions

### For Production Server

1. **Update the environment file** on the production server:
   ```bash
   # SSH into the VM
   gcloud compute ssh transparentsf-prod --project=euphoric-oath-467718-p3 --zone=us-central1-a
   
   # Edit the environment file
   sudo nano /opt/transparentsf/ai/.env
   ```
   
   Add these lines to the `.env` file:
   ```
   QDRANT_URL=localhost
   QDRANT_PORT=6333
   ```

2. **Deploy the updated code**:
   ```bash
   cd /opt/transparentsf
   sudo -u transparentsf git pull
   ```

3. **Install Docker and start Qdrant**:
   ```bash
   # Install Docker (if not already installed)
   curl -fsSL https://get.docker.com -o get-docker.sh
   sudo sh get-docker.sh
   sudo usermod -aG docker transparentsf
   rm get-docker.sh
   
   # Start Qdrant with Docker Compose
   cd /opt/transparentsf
   sudo -u transparentsf docker-compose up -d qdrant
   
   # Wait for Qdrant to be ready
   for i in {1..30}; do
       if curl -f http://localhost:6333/healthz >/dev/null 2>&1; then
           echo "Qdrant is ready!"
           break
       fi
       echo "Waiting for Qdrant... ($i/30)"
       sleep 2
   done
   ```

4. **Create and enable Qdrant systemd service**:
   ```bash
   sudo tee /etc/systemd/system/qdrant.service > /dev/null << 'EOF'
   [Unit]
   Description=Qdrant Vector Database
   After=docker.service
   Requires=docker.service

   [Service]
   Type=oneshot
   RemainAfterExit=yes
   User=transparentsf
   WorkingDirectory=/opt/transparentsf
   ExecStart=/usr/bin/docker-compose up -d qdrant
   ExecStop=/usr/bin/docker-compose down
   TimeoutStartSec=0

   [Install]
   WantedBy=multi-user.target
   EOF
   
   sudo systemctl daemon-reload
   sudo systemctl enable qdrant
   sudo systemctl start qdrant
   ```

5. **Restart the application**:
   ```bash
   sudo systemctl restart transparentsf
   ```

6. **Test the vector database reload**:
   ```bash
   # Check Qdrant health
   curl http://localhost:6333/healthz
   
   # Test the vector database reload functionality
   # (This should now work without the 404 error)
   ```

### Verification

1. **Check Qdrant status**:
   ```bash
   sudo systemctl status qdrant
   docker ps | grep qdrant
   ```

2. **Test vector database reload**:
   - Go to the application's backend interface
   - Try the "SF Public Metadata Reload" functionality
   - It should now complete successfully

3. **Check collections**:
   ```bash
   cd /opt/transparentsf/ai
   python tools/qdrant_manager.py collections
   ```

## Troubleshooting

### If Qdrant still fails to start:
1. Check Docker is running: `sudo systemctl status docker`
2. Check Qdrant logs: `docker logs qdrant`
3. Check systemd service: `sudo systemctl status qdrant`

### If vector reload still fails:
1. Verify environment variables: `grep QDRANT /opt/transparentsf/ai/.env`
2. Test connection: `python tools/qdrant_manager.py health`
3. Check application logs: `sudo journalctl -u transparentsf -f`

### If you need to reset Qdrant:
```bash
sudo systemctl stop qdrant
docker-compose down
docker volume rm transparentsf_qdrant_storage
sudo systemctl start qdrant
```

## Benefits

1. **Environment-based configuration**: Easy to change Qdrant settings without code changes
2. **Docker-based deployment**: Consistent and reliable Qdrant setup
3. **Health monitoring**: Built-in health checks and management tools
4. **Production-ready**: Proper systemd service and startup sequence
5. **Troubleshooting tools**: Management script for diagnostics

The vector database reload should now work properly in production!
