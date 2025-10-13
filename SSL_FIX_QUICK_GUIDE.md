# SSL Certificate Fix - Quick Guide

## Summary
Current certificate only valid for `beta.transparentsf.com`  
Need to support: `platform.transparentsf.com` and `beta.transparentsf.com`

## Prerequisites

**Before running these commands, verify DNS is pointing correctly:**

```bash
# From your local machine - both should return: 34.27.136.197
dig platform.transparentsf.com +short
dig beta.transparentsf.com +short
```

If not pointing correctly, update DNS A records at your registrar first!

## Step-by-Step Fix (Run on GCP VM)

### 1. SSH into VM
```bash
gcloud compute ssh transparentsf-prod --project=euphoric-oath-467718-p3 --zone=us-central1-a
```

### 2. Navigate to project directory
```bash
cd /opt/transparentsf
```

### 3. Pull latest changes
```bash
sudo -u transparentsf git fetch origin
sudo -u transparentsf git pull origin gcp-migration-complete
```

### 4. Update SSL Certificate
```bash
# Stop nginx temporarily
sudo systemctl stop nginx

# Request new certificate for both domains
sudo certbot certonly --standalone \
  -d platform.transparentsf.com \
  -d beta.transparentsf.com \
  --agree-tos \
  --non-interactive \
  --expand

# Start nginx again
sudo systemctl start nginx
```

### 5. Update Nginx Configuration
```bash
# Backup existing config
sudo cp /etc/nginx/sites-enabled/transparentsf /etc/nginx/sites-enabled/transparentsf.backup

# Copy new config
sudo cp /opt/transparentsf/nginx_transparentsf.conf /etc/nginx/sites-available/transparentsf

# Test configuration
sudo nginx -t
```

### 6. Reload Nginx
```bash
# If test passed, reload
sudo systemctl reload nginx

# Check status
sudo systemctl status nginx
```

### 7. Restart Application
```bash
# Restart the app to pick up CORS changes
sudo systemctl restart transparentsf

# Check status
sudo systemctl status transparentsf
```

### 8. Verify Certificate
```bash
# Should show both domains
sudo certbot certificates
```

## Test in Browser

- https://platform.transparentsf.com ✅
- https://beta.transparentsf.com ✅

## Troubleshooting

### If certbot fails with "Challenge failed"
```bash
# Check if port 80 is accessible
sudo netstat -tulpn | grep :80

# Check firewall
gcloud compute firewall-rules list --project=euphoric-oath-467718-p3 | grep allow-http
```

### If nginx test fails
```bash
# Check error logs
sudo tail -50 /var/log/nginx/error.log

# Verify certificate files exist
sudo ls -la /etc/letsencrypt/live/platform.transparentsf.com/
```

### If application not responding
```bash
# Check logs
sudo journalctl -u transparentsf -n 100 --no-pager

# Check if app is running
ps aux | grep uvicorn
```

## Quick Commands Reference

```bash
# View all certificates
sudo certbot certificates

# Test certificate renewal
sudo certbot renew --dry-run

# Check nginx config
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx

# Restart app
sudo systemctl restart transparentsf

# View app logs
sudo journalctl -u transparentsf -f

# View nginx logs
sudo tail -f /var/log/nginx/error.log
```

