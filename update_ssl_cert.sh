#!/bin/bash
# Update SSL Certificate for TransparentSF Domains
# Run this on your GCP VM
# Supports: platform.transparentsf.com and beta.transparentsf.com

set -e

echo "🔐 Updating SSL Certificate for TransparentSF domains..."
echo "   - platform.transparentsf.com"
echo "   - beta.transparentsf.com"
echo "   - dashboard.transparentsf.com"
echo ""

# Stop nginx to allow Certbot to use port 80
echo "Stopping nginx..."
sudo systemctl stop nginx

# Request certificate for all three domains
echo "Requesting SSL certificate from Let's Encrypt..."
sudo certbot certonly --standalone \
  -d platform.transparentsf.com \
  -d beta.transparentsf.com \
  -d dashboard.transparentsf.com \
  --agree-tos \
  --non-interactive \
  --expand

# Start nginx again
echo "Starting nginx..."
sudo systemctl start nginx

echo ""
echo "✅ SSL certificate updated successfully!"
echo ""
echo "📋 Next steps:"
echo "1. Update your nginx configuration to use the new certificate"
echo "2. Test with: sudo nginx -t"
echo "3. Reload nginx: sudo systemctl reload nginx"
echo ""
echo "Certificate location: /etc/letsencrypt/live/platform.transparentsf.com/"

