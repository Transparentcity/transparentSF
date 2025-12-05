# TransparentSF GCP Deployment Checklist

## Pre-Deployment Status ✅

### Environment Verification
- ✅ **GCP Project**: `euphoric-oath-467718-p3`
- ✅ **Authenticated**: `admin@transparentsf.com`
- ✅ **VM Instance**: `transparentsf-prod` (RUNNING)
- ✅ **External IP**: `34.27.136.197`
- ✅ **Git Branch**: `gcp-migration-complete`
- ✅ **Git Status**: Clean (no uncommitted changes)
- ✅ **Deployment Script**: Executable

### Infrastructure Components
- ✅ **Compute Engine VM**: e2-medium in us-central1-a
- ✅ **Cloud SQL**: PostgreSQL (34.28.89.105:5432)
- ✅ **Qdrant Vector DB**: Docker container (port 6333)
- ✅ **Nginx**: Reverse proxy (port 80 → 8000)

## Deployment Steps

### Option 1: Automated Deployment (Recommended)

Run the automated deployment script:

```bash
cd /Users/simongoldman/Documents/TransparentSF_from_git/transparentSF
./deploy_to_gcp.sh
```

This script will:
1. ✅ Verify git status
2. 🔄 Commit changes (if any)
3. 🚀 Push to remote repository
4. 📡 Connect to VM
5. 🔄 Pull latest code
6. 📦 Install dependencies
7. 🔄 Restart services
8. ✅ Verify deployment
9. 🧪 Test endpoints

### Option 2: Manual Deployment

If you prefer manual control:

```bash
# 1. SSH into the VM
gcloud compute ssh transparentsf-prod \
  --project=euphoric-oath-467718-p3 \
  --zone=us-central1-a

# 2. Update code
cd /opt/transparentsf
sudo -u transparentsf git pull origin gcp-migration-complete

# 3. Update dependencies
sudo -u transparentsf /opt/transparentsf/venv/bin/pip install -r requirements.txt

# 4. Restart application
sudo systemctl restart transparentsf

# 5. Check status
sudo systemctl status transparentsf
sudo journalctl -u transparentsf -n 50

# 6. Test the application
curl http://localhost:8000/
```

## Post-Deployment Verification

### 1. Check Application Status
```bash
# View recent logs
gcloud compute ssh transparentsf-prod \
  --project=euphoric-oath-467718-p3 \
  --zone=us-central1-a \
  --command="sudo journalctl -u transparentsf -n 100 --no-pager"
```

### 2. Test Endpoints
```bash
# Test main application
curl http://34.27.136.197/

# Test API endpoints
curl http://34.27.136.197/api/metrics
curl http://34.27.136.197/backend/api/system-status

# Test chat interface
curl -X POST http://34.27.136.197/chat/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'
```

### 3. Verify Services
```bash
gcloud compute ssh transparentsf-prod \
  --project=euphoric-oath-467718-p3 \
  --zone=us-central1-a \
  --command="sudo systemctl status transparentsf nginx qdrant"
```

### 4. Check Resource Usage
```bash
gcloud compute ssh transparentsf-prod \
  --project=euphoric-oath-467718-p3 \
  --zone=us-central1-a \
  --command="df -h && free -h && top -bn1 | head -20"
```

## Monitoring Commands

### Real-time Logs
```bash
# Application logs
gcloud compute ssh transparentsf-prod \
  --project=euphoric-oath-467718-p3 \
  --zone=us-central1-a \
  --command="sudo journalctl -u transparentsf -f"

# Nginx access logs
gcloud compute ssh transparentsf-prod \
  --project=euphoric-oath-467718-p3 \
  --zone=us-central1-a \
  --command="sudo tail -f /var/log/nginx/access.log"
```

### System Health
```bash
# Check all services
gcloud compute ssh transparentsf-prod \
  --project=euphoric-oath-467718-p3 \
  --zone=us-central1-a \
  --command="sudo systemctl list-units --type=service --state=running | grep -E 'transparentsf|nginx|qdrant'"
```

## Rollback Procedure

If the deployment fails:

```bash
# SSH into VM
gcloud compute ssh transparentsf-prod \
  --project=euphoric-oath-467718-p3 \
  --zone=us-central1-a

# Rollback to previous commit
cd /opt/transparentsf
sudo -u transparentsf git log --oneline -n 10
sudo -u transparentsf git reset --hard <previous-commit-hash>

# Restart services
sudo systemctl restart transparentsf

# Verify rollback
sudo systemctl status transparentsf
```

## Troubleshooting

### Common Issues

#### Application Won't Start
```bash
# Check logs for errors
sudo journalctl -u transparentsf -n 100

# Check environment variables
sudo -u transparentsf cat /opt/transparentsf/ai/.env

# Test Python environment
cd /opt/transparentsf
source venv/bin/activate
python -c "import fastapi; print('FastAPI OK')"
```

#### Database Connection Issues
```bash
# Test database connection
cd /opt/transparentsf/ai
python tools/test_db_connection.py

# Check database credentials
grep DATABASE_URL /opt/transparentsf/ai/.env
```

#### Qdrant Not Accessible
```bash
# Check Qdrant status
sudo systemctl status qdrant
docker ps | grep qdrant

# Restart Qdrant
sudo systemctl restart qdrant

# Test Qdrant health
curl http://localhost:6333/healthz
```

#### Port Conflicts
```bash
# Check what's using ports
sudo lsof -i :8000
sudo lsof -i :80
sudo lsof -i :6333
```

## Security Checklist

- [ ] `.env` file not committed to git
- [ ] Service account key stored securely
- [ ] Database credentials rotated
- [ ] Firewall rules configured
- [ ] SSL/TLS certificates valid
- [ ] API keys environment-specific

## Performance Optimization

### After Deployment
1. Monitor memory usage: `free -h`
2. Check disk space: `df -h`
3. Review slow queries in PostgreSQL
4. Monitor Qdrant performance
5. Check Nginx access logs for traffic patterns

### Scaling Considerations
- Current: e2-medium (2 vCPUs, 4 GB memory)
- If needed, upgrade to e2-standard-2 or e2-standard-4
- Consider Cloud Load Balancing for multiple instances
- Set up Cloud CDN for static assets

## Backup Verification

Before major deployments:
```bash
# Backup database
gcloud sql export sql transparentsf-db-20250914 \
  gs://your-bucket/backups/pre-deployment-$(date +%Y%m%d).sql \
  --database=transparentsf

# Backup application files
gcloud compute ssh transparentsf-prod \
  --command="sudo tar -czf /tmp/transparentsf-backup-$(date +%Y%m%d).tar.gz /opt/transparentsf"
```

## Success Criteria

Deployment is successful when:
- ✅ Application responds on http://34.27.136.197/
- ✅ API endpoints return valid responses
- ✅ Database queries execute successfully
- ✅ Qdrant vector search is operational
- ✅ No critical errors in logs (past 5 minutes)
- ✅ Memory usage < 80%
- ✅ Disk usage < 80%
- ✅ All systemd services are active

## Post-Deployment Tasks

1. **Test Key Features**:
   - [ ] Chat interface
   - [ ] Metrics dashboard
   - [ ] Data analytics
   - [ ] Report generation
   - [ ] Anomaly detection

2. **Update Documentation**:
   - [ ] Update CHANGELOG.md
   - [ ] Document new features
   - [ ] Update API documentation

3. **Notify Stakeholders**:
   - [ ] Send deployment notification
   - [ ] Update status page (if applicable)
   - [ ] Monitor for user feedback

## Emergency Contacts

- **GCP Console**: https://console.cloud.google.com/
- **Project ID**: euphoric-oath-467718-p3
- **VM Dashboard**: https://console.cloud.google.com/compute/instances
- **Cloud SQL**: https://console.cloud.google.com/sql/instances

## Quick Reference Commands

```bash
# One-liner to check everything
gcloud compute ssh transparentsf-prod --project=euphoric-oath-467718-p3 --zone=us-central1-a --command="echo '=== Services ===' && sudo systemctl status transparentsf nginx qdrant --no-pager && echo '=== Resources ===' && df -h && free -h && echo '=== Recent Logs ===' && sudo journalctl -u transparentsf -n 20 --no-pager"

# Quick restart
gcloud compute ssh transparentsf-prod --project=euphoric-oath-467718-p3 --zone=us-central1-a --command="sudo systemctl restart transparentsf && sleep 5 && sudo systemctl status transparentsf --no-pager"

# View live logs
gcloud compute ssh transparentsf-prod --project=euphoric-oath-467718-p3 --zone=us-central1-a --command="sudo journalctl -u transparentsf -f"
```

---

**Last Updated**: $(date)
**Deployment Branch**: gcp-migration-complete
**Status**: Ready for deployment ✅

