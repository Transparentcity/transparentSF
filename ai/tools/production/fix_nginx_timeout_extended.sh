#!/bin/bash

# Script to fix nginx timeout issues on production instance with extended timeouts
# This script updates the nginx configuration with proper timeout settings for heavy operations

echo "Fixing nginx timeout configuration with extended timeouts..."

# Create the improved nginx configuration with extended timeouts
sudo tee /etc/nginx/sites-available/transparentsf > /dev/null << 'NGINX_EOF'
server {
    listen 80;
    server_name _;

    # Increase client body size for file uploads
    client_max_body_size 50M;

    # Global timeout settings - increased for heavy operations
    proxy_connect_timeout 600s;
    proxy_send_timeout 900s;
    proxy_read_timeout 900s;
    send_timeout 900s;

    # General location with extended timeouts
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Extended timeout settings for this location
        proxy_connect_timeout 600s;
        proxy_send_timeout 900s;
        proxy_read_timeout 900s;
        
        # Buffer settings for better performance
        proxy_buffering on;
        proxy_buffer_size 8k;
        proxy_buffers 16 8k;
        proxy_busy_buffers_size 16k;
        
        # Keep-alive settings
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    # Static files
    location /static/ {
        alias /opt/transparentsf/ai/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # API routes with maximum timeouts for heavy operations
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Maximum timeouts for API calls that might take very long
        proxy_connect_timeout 600s;
        proxy_send_timeout 1200s;  # 20 minutes
        proxy_read_timeout 1200s; # 20 minutes
        
        # Buffer settings
        proxy_buffering on;
        proxy_buffer_size 16k;
        proxy_buffers 32 16k;
        proxy_busy_buffers_size 32k;
        
        # Keep-alive settings
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    # Backend routes with maximum timeouts for heavy operations
    location /backend/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Maximum timeouts for backend operations
        proxy_connect_timeout 600s;
        proxy_send_timeout 1200s;  # 20 minutes
        proxy_read_timeout 1200s; # 20 minutes
        
        # Buffer settings
        proxy_buffering on;
        proxy_buffer_size 16k;
        proxy_buffers 32 16k;
        proxy_busy_buffers_size 32k;
        
        # Keep-alive settings
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    # Specific location for vacancy analysis endpoints (known to be heavy)
    location ~ ^/(api/vacancy|backend/vacancy) {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Maximum timeouts for vacancy analysis
        proxy_connect_timeout 600s;
        proxy_send_timeout 1800s;  # 30 minutes
        proxy_read_timeout 1800s; # 30 minutes
        
        # Large buffer settings for big data responses
        proxy_buffering on;
        proxy_buffer_size 32k;
        proxy_buffers 64 32k;
        proxy_busy_buffers_size 64k;
        
        # Keep-alive settings
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    # Specific location for database admin operations
    location ~ ^/(backend/database|api/database) {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Maximum timeouts for database operations
        proxy_connect_timeout 600s;
        proxy_send_timeout 1800s;  # 30 minutes
        proxy_read_timeout 1800s; # 30 minutes
        
        # Large buffer settings
        proxy_buffering on;
        proxy_buffer_size 32k;
        proxy_buffers 64 32k;
        proxy_busy_buffers_size 64k;
        
        # Keep-alive settings
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }
}
NGINX_EOF

# Test the nginx configuration
echo "Testing nginx configuration..."
sudo nginx -t

if [ $? -eq 0 ]; then
    echo "Nginx configuration test passed. Reloading nginx..."
    sudo systemctl reload nginx
    echo "✅ Nginx configuration updated successfully!"
    echo ""
    echo "📋 Updated timeout settings:"
    echo "   • General routes: 15 minutes"
    echo "   • API routes: 20 minutes"
    echo "   • Backend routes: 20 minutes"
    echo "   • Vacancy analysis: 30 minutes"
    echo "   • Database operations: 30 minutes"
    echo ""
    echo "🔄 Nginx has been reloaded with new configuration."
else
    echo "❌ Nginx configuration test failed. Please check the configuration."
    exit 1
fi
