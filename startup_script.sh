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
git clone https://github.com/robjective/transparentSF.git .

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
sudo systemctl start transparentsf

# Install and configure Nginx
sudo apt-get install -y nginx

# Create Nginx configuration
sudo tee /etc/nginx/sites-available/transparentsf > /dev/null << 'NGINX_EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Static files
    location /static/ {
        alias /opt/transparentsf/ai/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
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
