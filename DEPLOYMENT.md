# TransparentSF Deployment Guide

## Current Deployment Configuration

The application is currently deployed on Google Cloud Platform with the following configuration:

### Project Details
- **Project ID**: `euphoric-oath-467718-p3`
- **Region**: `us-central1`
- **Zone**: `us-central1-a`

### Components
1. **Compute Engine VM**
   - **Instance Name**: `transparentsf-prod`
   - **Machine Type**: `e2-medium`
   - **External IP**: `34.27.136.197`
   - **Service Account**: Uses service account key

2. **Cloud SQL**
   - **Instance Name**: `transparentsf-db-20250914`
   - **Database**: PostgreSQL
   - **Connection**: `postgresql://transparentsf:YOUR_PASSWORD@34.28.89.105:5432/transparentsf`

3. **Docker Services**
   - **Qdrant Vector Database**: Running in Docker container on port 6333
   - **Environment Variables**: `QDRANT_URL=localhost` and `QDRANT_PORT=6333`

## Deployment Process

### 1. Push Code Changes
```bash
# From your local machine
git add .
git commit -m "Your commit message"
git push origin main
```

### 2. Update Server
```bash
# SSH into the VM
gcloud compute ssh transparentsf-prod --project=euphoric-oath-467718-p3 --zone=us-central1-a

# Update code
cd /opt/transparentsf
sudo -u transparentsf git pull

# Restart the application
sudo systemctl restart transparentsf

# Optional: Check status
sudo systemctl status transparentsf
```

### 3. Monitor Logs
```bash
# Application logs
sudo journalctl -u transparentsf -f

# Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

## Important Files and Directories

### On VM
- **Application Root**: `/opt/transparentsf/`
- **Environment File**: `/opt/transparentsf/ai/.env`
- **Service Account Key**: `/opt/transparentsf/ai/credentials/euphoric-oath-467718-p3-164f26165c3c.json`
- **Log Directory**: `/opt/transparentsf/ai/logs/`
- **Systemd Service**: `/etc/systemd/system/transparentsf.service`
- **Nginx Config**: `/etc/nginx/sites-enabled/transparentsf`

### In Repository
- **GCP Migration Guide**: `GCP_MIGRATION_GUIDE.md`
- **Environment Template**: `ai/gcp_env_example.env`
- **Deployment Guide**: `DEPLOYMENT.md` (this file)

## Security Notes

1. The service account key and `.env` file contain sensitive information and are not committed to the repository
2. The application runs as the `transparentsf` user for security
3. Nginx is configured to proxy requests from port 80 to the application on port 8000
4. Database access is restricted by IP address

## Troubleshooting

### Common Issues

1. **Application Not Responding**
   ```bash
   # Check application status
   sudo systemctl status transparentsf
   
   # Check logs
   sudo journalctl -u transparentsf -n 100
   ```

2. **Database Connection Issues**
   ```bash
   # Test database connection
   cd /opt/transparentsf/ai
   python tools/test_db_connection.py
   ```

3. **Qdrant Issues**
   ```bash
   # Check Qdrant service status
   sudo systemctl status qdrant
   
   # Check Docker container
   docker ps | grep qdrant
   
   # Restart Qdrant if needed
   sudo systemctl restart qdrant
   
   # Or restart Docker container directly
   docker restart qdrant
   
   # Check Qdrant health
   curl http://localhost:6333/healthz
   ```

4. **Permission Issues**
   ```bash
   # Fix log directory permissions
   sudo chown -R transparentsf:transparentsf /opt/transparentsf/ai/logs
   sudo chmod 755 /opt/transparentsf/ai/logs
   ```

## Maintenance

### Regular Tasks
1. Monitor disk usage
2. Check and rotate logs
3. Update system packages
4. Backup database
5. Test disaster recovery procedures

### Backup Procedures
- Database backups are stored in `/backup_history/`
- Use `tools/cloud_sql_migration.py` for database operations
- Keep service account key and `.env` backups secure

For more detailed information about the GCP setup and migration process, refer to `GCP_MIGRATION_GUIDE.md`.
