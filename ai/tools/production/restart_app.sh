#!/bin/bash

# Script to restart the TransparentSF application and check its status
# This script helps recover from crashes and ensures the app is running properly

echo "🔄 Restarting TransparentSF Application..."
echo "=========================================="

# 1. Stop the application service
echo "1. Stopping TransparentSF service..."
sudo systemctl stop transparentsf

# Wait a moment for the service to stop
sleep 3

# 2. Check if any Python processes are still running
echo "2. Checking for any remaining Python processes..."
python_processes=$(ps aux | grep python | grep main.py | grep -v grep)
if [ ! -z "$python_processes" ]; then
    echo "Found remaining Python processes, killing them..."
    sudo pkill -f "python.*main.py"
    sleep 2
else
    echo "No remaining Python processes found."
fi

# 3. Check if port 8000 is still in use
echo "3. Checking if port 8000 is still in use..."
port_8000_usage=$(sudo netstat -tlnp | grep :8000)
if [ ! -z "$port_8000_usage" ]; then
    echo "Port 8000 is still in use:"
    echo "$port_8000_usage"
    echo "Killing processes using port 8000..."
    sudo fuser -k 8000/tcp
    sleep 2
else
    echo "Port 8000 is free."
fi

# 4. Start the application service
echo "4. Starting TransparentSF service..."
sudo systemctl start transparentsf

# Wait for the service to start
sleep 5

# 5. Check service status
echo "5. Checking service status..."
sudo systemctl status transparentsf --no-pager

# 6. Check if the application is responding
echo "6. Testing application connectivity..."
sleep 10  # Give the app more time to start

for i in {1..5}; do
    echo "Attempt $i/5: Testing local connection..."
    if curl -s --max-time 10 http://127.0.0.1:8000/ > /dev/null; then
        echo "✅ Application is responding on localhost:8000"
        break
    else
        echo "❌ Application not responding yet, waiting..."
        sleep 5
    fi
done

# 7. Check nginx status
echo "7. Checking nginx status..."
sudo systemctl status nginx --no-pager

# 8. Test external connectivity
echo "8. Testing external connectivity..."
external_ip=$(curl -s ifconfig.me)
echo "External IP: $external_ip"

if curl -s --max-time 10 http://$external_ip/ > /dev/null; then
    echo "✅ Application is accessible externally"
else
    echo "❌ Application is not accessible externally"
fi

# 9. Show recent logs
echo "9. Recent application logs:"
sudo journalctl -u transparentsf --no-pager -n 20

echo ""
echo "🔍 Restart Complete!"
echo "==================="
echo ""
echo "If the application is still not working:"
echo "1. Check the logs: sudo journalctl -u transparentsf -f"
echo "2. Check nginx logs: sudo tail -f /var/log/nginx/error.log"
echo "3. Verify database connectivity"
echo "4. Check environment variables in /opt/transparentsf/ai/.env"
echo ""
echo "To monitor the application in real-time:"
echo "sudo journalctl -u transparentsf -f"
