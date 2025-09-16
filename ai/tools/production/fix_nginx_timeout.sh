#!/bin/bash

# Script to fix nginx timeout issues on production instance
# This script updates the nginx configuration with proper timeout settings

echo "Fixing nginx timeout configuration..."

# Create the improved nginx configuration
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

# Test the nginx configuration
echo "Testing nginx configuration..."
sudo nginx -t

if [ $? -eq 0 ]; then
    echo "Nginx configuration test passed. Reloading nginx..."
    sudo systemctl reload nginx
    echo "Nginx reloaded successfully!"
    
    # Check nginx status
    echo "Checking nginx status..."
    sudo systemctl status nginx --no-pager
    
    # Check if the application is running
    echo "Checking application status..."
    sudo systemctl status transparentsf --no-pager
    
    echo ""
    echo "✅ Nginx timeout configuration has been updated successfully!"
    echo "The following timeout settings have been applied:"
    echo "  - General timeouts: 300 seconds (5 minutes)"
    echo "  - API routes (/api/): 600 seconds (10 minutes)"
    echo "  - Backend routes (/backend/): 600 seconds (10 minutes)"
    echo ""
    echo "Your application should now handle longer-running requests without timing out."
    
else
    echo "❌ Nginx configuration test failed. Please check the configuration."
    exit 1
fi
