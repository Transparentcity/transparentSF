#!/bin/bash

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

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
usermod -aG docker transparentsf
rm get-docker.sh

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

# Start Qdrant with Docker Compose
cd /opt/transparentsf
sudo -u transparentsf docker-compose up -d qdrant

# Wait for Qdrant to be ready
echo "Waiting for Qdrant to be ready..."
for i in {1..30}; do
    if curl -f http://localhost:6333/healthz >/dev/null 2>&1; then
        echo "Qdrant is ready!"
        break
    fi
    echo "Waiting for Qdrant... ($i/30)"
    sleep 2
done

# Create Qdrant systemd service
sudo tee /etc/systemd/system/qdrant.service > /dev/null << 'QDRANT_SERVICE_EOF'
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
QDRANT_SERVICE_EOF

# Enable and start Qdrant service
sudo systemctl daemon-reload
sudo systemctl enable qdrant
sudo systemctl start qdrant

# Start the application service
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
