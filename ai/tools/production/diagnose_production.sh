#!/bin/bash

# Script to diagnose production instance issues
# This script checks various components and logs to identify problems

echo "🔍 Diagnosing TransparentSF Production Instance..."
echo "=================================================="

# Check if we're running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    echo "⚠️  This script should be run with sudo for full diagnostics"
    echo "   Some checks may be limited without root access"
    echo ""
fi

# 1. Check system resources
echo "📊 System Resources:"
echo "-------------------"
echo "Memory usage:"
free -h
echo ""
echo "Disk usage:"
df -h
echo ""
echo "CPU load:"
uptime
echo ""

# 2. Check nginx status
echo "🌐 Nginx Status:"
echo "---------------"
if command -v nginx &> /dev/null; then
    echo "Nginx version:"
    nginx -v
    echo ""
    echo "Nginx service status:"
    sudo systemctl status nginx --no-pager -l
    echo ""
    echo "Nginx configuration test:"
    sudo nginx -t
    echo ""
    echo "Nginx processes:"
    ps aux | grep nginx | grep -v grep
    echo ""
else
    echo "❌ Nginx not found"
fi

# 3. Check application status
echo "🚀 Application Status:"
echo "---------------------"
echo "TransparentSF service status:"
sudo systemctl status transparentsf --no-pager -l
echo ""
echo "Application processes:"
ps aux | grep python | grep main.py | grep -v grep
echo ""

# 4. Check if application is responding locally
echo "🔗 Local Application Connectivity:"
echo "---------------------------------"
echo "Testing local connection to port 8000..."
if curl -s --max-time 10 http://127.0.0.1:8000/ > /dev/null; then
    echo "✅ Application is responding on localhost:8000"
else
    echo "❌ Application is NOT responding on localhost:8000"
    echo "   This indicates the FastAPI application is not running or not accessible"
fi
echo ""

# 5. Check nginx error logs
echo "📝 Recent Nginx Error Logs:"
echo "---------------------------"
if [ -f /var/log/nginx/error.log ]; then
    echo "Last 20 lines of nginx error log:"
    sudo tail -20 /var/log/nginx/error.log
else
    echo "❌ Nginx error log not found"
fi
echo ""

# 6. Check nginx access logs
echo "📝 Recent Nginx Access Logs:"
echo "----------------------------"
if [ -f /var/log/nginx/access.log ]; then
    echo "Last 10 lines of nginx access log:"
    sudo tail -10 /var/log/nginx/access.log
else
    echo "❌ Nginx access log not found"
fi
echo ""

# 7. Check application logs
echo "📝 Recent Application Logs:"
echo "---------------------------"
if [ -f /opt/transparentsf/ai/logs/app.log ]; then
    echo "Last 20 lines of application log:"
    sudo tail -20 /opt/transparentsf/ai/logs/app.log
else
    echo "❌ Application log not found at /opt/transparentsf/ai/logs/app.log"
fi
echo ""

# 8. Check systemd journal for the application
echo "📝 Systemd Journal for TransparentSF:"
echo "-------------------------------------"
sudo journalctl -u transparentsf --no-pager -n 20
echo ""

# 9. Check network connectivity
echo "🌍 Network Connectivity:"
echo "-----------------------"
echo "Checking if port 80 is listening:"
sudo netstat -tlnp | grep :80
echo ""
echo "Checking if port 8000 is listening:"
sudo netstat -tlnp | grep :8000
echo ""

# 10. Check firewall status
echo "🔥 Firewall Status:"
echo "------------------"
if command -v ufw &> /dev/null; then
    sudo ufw status
else
    echo "UFW not found, checking iptables:"
    sudo iptables -L -n | head -20
fi
echo ""

# 11. Check environment and configuration
echo "⚙️  Environment Check:"
echo "--------------------"
echo "Python version:"
python3 --version
echo ""
echo "Virtual environment status:"
if [ -d "/opt/transparentsf/venv" ]; then
    echo "✅ Virtual environment exists"
    echo "Virtual environment Python:"
    /opt/transparentsf/venv/bin/python --version
else
    echo "❌ Virtual environment not found"
fi
echo ""

echo "🔍 Diagnosis Complete!"
echo "====================="
echo ""
echo "Next steps based on common issues:"
echo "1. If nginx is not running: sudo systemctl start nginx"
echo "2. If application is not running: sudo systemctl start transparentsf"
echo "3. If you see timeout errors: Run ./fix_nginx_timeout.sh"
echo "4. If application logs show errors: Check database connectivity and environment variables"
echo "5. If port 8000 is not listening: The FastAPI application may have crashed"
echo ""
echo "For detailed logs, check:"
echo "- Application logs: /opt/transparentsf/ai/logs/"
echo "- Nginx logs: /var/log/nginx/"
echo "- System logs: sudo journalctl -u transparentsf -f"
