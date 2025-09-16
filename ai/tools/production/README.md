# Production Maintenance Tools

This directory contains scripts and tools for maintaining the TransparentSF production deployment on Google Cloud Platform.

## Scripts

### Application Management

#### `restart_app.sh`
**Purpose**: Comprehensive application restart with health checks  
**Usage**: `./restart_app.sh`  
**When to use**: 
- Application crashes or becomes unresponsive
- After code deployments
- When port 8000 is stuck or occupied
- General application recovery

**Features**:
- Stops TransparentSF service cleanly
- Kills any remaining Python processes
- Frees up port 8000 if stuck
- Restarts the application service
- Tests local and external connectivity
- Shows recent logs for troubleshooting

#### `diagnose_production.sh`
**Purpose**: Comprehensive system diagnostics and troubleshooting  
**Usage**: `./diagnose_production.sh`  
**When to use**:
- Application is not responding
- Need to troubleshoot system issues
- Regular health checks
- Before and after deployments

**Features**:
- System resource monitoring (CPU, memory, disk)
- Nginx status and configuration check
- Application service status
- Network connectivity tests
- Log analysis (nginx, application, systemd)
- Firewall status check
- Environment verification

### Configuration Management

#### `fix_nginx_timeout.sh`
**Purpose**: Fixes nginx timeout configuration issues  
**Usage**: `./fix_nginx_timeout.sh`  
**When to use**:
- API calls timing out
- Backend operations taking too long
- Long-running requests failing
- After adding new heavy endpoints

**Features**:
- Updates nginx configuration with proper timeouts
- Different timeout settings for different route types:
  - General routes: 300 seconds (5 minutes)
  - API routes (`/api/`): 600 seconds (10 minutes)
  - Backend routes (`/backend/`): 600 seconds (10 minutes)
- Tests configuration before applying
- Reloads nginx safely

#### `startup_script.sh`
**Purpose**: Initial server setup and configuration  
**Usage**: `./startup_script.sh`  
**When to use**:
- Setting up new production instances
- Disaster recovery scenarios
- Rebuilding the server from scratch

**Features**:
- System package updates
- Python environment setup
- Service account configuration
- Database connection setup
- Application service configuration
- Nginx configuration
- Firewall setup
- Log directory creation

### Data Management

#### `debug_stale_data.py`
**Purpose**: Debugging stale data issues  
**Usage**: `python debug_stale_data.py`  
**When to use**:
- Data appears outdated
- Metrics not updating properly
- Need to verify data freshness
- Troubleshooting data pipeline issues

**Features**:
- Checks data freshness across different sources
- Identifies stale datasets
- Provides recommendations for data updates
- Logs data pipeline status

## Usage Guidelines

### Before Running Scripts
1. **SSH into the production server**:
   ```bash
   gcloud compute ssh transparentsf-prod --project=euphoric-oath-467718-p3 --zone=us-central1-a
   ```

2. **Navigate to the tools directory**:
   ```bash
   cd /opt/transparentsf/ai/tools/production
   ```

3. **Make scripts executable** (if needed):
   ```bash
   chmod +x *.sh
   ```

### Common Workflows

#### Application Recovery
```bash
# 1. Diagnose the issue
./diagnose_production.sh

# 2. Restart the application
./restart_app.sh

# 3. If still having issues, check logs
sudo journalctl -u transparentsf -f
```

#### After Code Deployment
```bash
# 1. Update code (from deployment process)
cd /opt/transparentsf
sudo -u transparentsf git pull

# 2. Restart application
cd ai/tools/production
./restart_app.sh

# 3. Verify everything is working
./diagnose_production.sh
```

#### Handling Timeout Issues
```bash
# 1. Fix nginx timeouts
./fix_nginx_timeout.sh

# 2. Restart application
./restart_app.sh

# 3. Test the problematic endpoints
```

#### New Server Setup
```bash
# 1. Run the startup script
./startup_script.sh

# 2. Verify setup
./diagnose_production.sh

# 3. Test application
./restart_app.sh
```

## Security Notes

- All scripts should be run with appropriate permissions
- Some scripts require `sudo` for full functionality
- Scripts contain production server paths and configurations
- Keep these scripts secure and don't expose them publicly

## Monitoring and Logs

### Key Log Locations
- **Application logs**: `/opt/transparentsf/ai/logs/`
- **Nginx logs**: `/var/log/nginx/`
- **System logs**: `sudo journalctl -u transparentsf -f`

### Health Check Endpoints
- **Main application**: `http://localhost:8000/`
- **Backend interface**: `http://localhost:8000/backend`
- **External access**: `http://34.58.112.164/`

## Troubleshooting

### Common Issues

1. **Permission Denied**
   ```bash
   sudo chmod +x *.sh
   sudo chown transparentsf:transparentsf *.sh
   ```

2. **Scripts Not Found**
   - Ensure you're in the correct directory: `/opt/transparentsf/ai/tools/production`
   - Check if files were moved during deployment

3. **Service Won't Start**
   - Check logs: `sudo journalctl -u transparentsf -n 50`
   - Verify environment variables in `/opt/transparentsf/ai/.env`
   - Check database connectivity

4. **Nginx Issues**
   - Test configuration: `sudo nginx -t`
   - Check nginx logs: `sudo tail -f /var/log/nginx/error.log`
   - Restart nginx: `sudo systemctl restart nginx`

## Maintenance Schedule

### Daily
- Monitor application status
- Check error logs

### Weekly
- Run `diagnose_production.sh` for health check
- Review application logs
- Check disk space and system resources

### Monthly
- Update system packages
- Review and rotate logs
- Test disaster recovery procedures
- Backup database

For more information about the production deployment, see the main [DEPLOYMENT.md](../../../DEPLOYMENT.md) file.
