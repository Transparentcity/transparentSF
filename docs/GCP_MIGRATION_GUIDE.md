# TransparentSF Google Cloud Platform Migration Guide

This guide provides step-by-step instructions for migrating your TransparentSF application from your current hosting environment to Google Cloud Platform.

## 🎯 **Migration Overview**

The migration will move your application to:
- **Database**: Google Cloud SQL (PostgreSQL)
- **Storage**: Google Cloud Storage (GCS)
- **Compute**: Google Compute Engine VM
- **Monitoring**: Google Cloud Monitoring & Logging

## 📋 **Prerequisites**

### 1. Google Cloud Account Setup
- [ ] Create a Google Cloud account
- [ ] Create a new project or select existing project
- [ ] Enable billing for the project
- [ ] Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install

### 2. Local Environment Setup
- [ ] Install Google Cloud SDK
- [ ] Authenticate with gcloud: `gcloud auth login`
- [ ] Set your project: `gcloud config set project YOUR_PROJECT_ID`
- [ ] Install PostgreSQL client tools (for database migration)

### 3. Current Application Backup
- [ ] Backup your current database
- [ ] Backup your current application files
- [ ] Document your current environment variables

## 🚀 **Migration Steps**

### **Step 1: Prepare Your Environment**

1. **Activate your virtual environment:**
   ```bash
   cd /path/to/your/transparentSF/repository
   source venv/bin/activate
   ```

2. **Install additional dependencies:**
   ```bash
   cd ai
   pip install google-cloud-storage google-auth google-auth-oauthlib google-auth-httplib2
   ```

3. **Set up Google Cloud credentials:**
   ```bash
   # Option A: Service Account (Recommended for production)
   # 1. Go to Google Cloud Console > IAM & Admin > Service Accounts
   # 2. Create a new service account with Storage Admin and Cloud SQL Admin roles
   # 3. Download the JSON key file
   # 4. Set environment variable:
   export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/service-account-key.json"
   
   # Option B: Default credentials (For development)
   gcloud auth application-default login
   ```

### **Step 2: Run the Complete Migration**

The migration script will handle everything automatically:

```bash
cd ai
python tools/gcp_migration.py --project-id YOUR_PROJECT_ID --region us-central1
```

**What the script does:**
1. ✅ Enables required Google Cloud APIs
2. ✅ Exports your current database
3. ✅ Creates Cloud SQL PostgreSQL instance
4. ✅ Imports your database to Cloud SQL
5. ✅ Sets up Google Cloud Storage bucket
6. ✅ Migrates your files to GCS
7. ✅ Creates Compute Engine VM
8. ✅ Deploys your application
9. ✅ Configures monitoring

### **Step 3: Configure Your Domain (Optional)**

If you have a custom domain:

1. **Point your domain to the VM's external IP:**
   ```bash
   # Get the VM's external IP
   gcloud compute instances describe transparentsf-app-YYYYMMDD \
     --project=YOUR_PROJECT_ID \
     --zone=us-central1-a \
     --format="get(networkInterfaces[0].accessConfigs[0].natIP)"
   ```

2. **Set up SSL certificate:**
   ```bash
   # SSH into your VM
   gcloud compute ssh transparentsf-app-YYYYMMDD --project=YOUR_PROJECT_ID --zone=us-central1-a
   
   # Install Certbot
   sudo apt-get install certbot python3-certbot-nginx
   
   # Get SSL certificate
   sudo certbot --nginx -d your-domain.com
   ```

### **Step 4: Update Environment Variables**

1. **Copy the GCP environment template:**
   ```bash
   cp ai/gcp_env_example.env ai/.env
   ```

2. **Update the .env file with your actual values:**
   - Replace `YOUR_PROJECT_ID` with your actual project ID
   - Replace `YOUR_DATABASE_PASSWORD` with the generated password
   - Update API keys from your existing configuration
   - Set your custom domain if applicable

3. **Deploy the updated configuration:**
   ```bash
   # Copy updated .env to VM
   gcloud compute scp ai/.env transparentsf-app-YYYYMMDD:~/ \
     --project=YOUR_PROJECT_ID --zone=us-central1-a
   
   # SSH into VM and update configuration
   gcloud compute ssh transparentsf-app-YYYYMMDD \
     --project=YOUR_PROJECT_ID --zone=us-central1-a \
     --command="sudo cp ~/.env /opt/transparentsf/ai/ && sudo systemctl restart transparentsf"
   ```

## 🔧 **Manual Migration Steps (Alternative)**

If you prefer to run the migration step by step:

### **Database Migration Only:**
```bash
python tools/cloud_sql_migration.py \
  --project-id YOUR_PROJECT_ID \
  --instance-name transparentsf-db-YYYYMMDD \
  --region us-central1
```

### **File Storage Migration Only:**
```bash
# First, set up GCS configuration
cp ai/gcs_config_example.env ai/.env
# Edit ai/.env with your GCS settings

# Then run the migration
python tools/migrate_to_gcs.py
```

### **Compute Engine Setup Only:**
```bash
python tools/compute_engine_setup.py \
  --project-id YOUR_PROJECT_ID \
  --instance-name transparentsf-app-YYYYMMDD \
  --zone us-central1-a
```

## 📊 **Post-Migration Verification**

### **1. Check Application Status:**
```bash
# SSH into your VM
gcloud compute ssh transparentsf-app-YYYYMMDD --project=YOUR_PROJECT_ID --zone=us-central1-a

# Check application status
sudo systemctl status transparentsf

# Check application logs
sudo journalctl -u transparentsf -f
```

### **2. Test Database Connection:**
```bash
# Test database connectivity
python tools/test_db_connection.py
```

### **3. Test File Storage:**
```bash
# Test GCS connectivity
python tools/setup_gcs.py --test
```

### **4. Verify Application Functionality:**
- [ ] Access your application via the VM's external IP
- [ ] Test data visualization features
- [ ] Verify file uploads/downloads work
- [ ] Check that reports are generated correctly

## 💰 **Cost Optimization**

### **Estimated Monthly Costs:**
- **Cloud SQL (db-f1-micro)**: ~$7-15/month
- **Compute Engine (e2-medium)**: ~$25-35/month
- **Cloud Storage**: ~$1-5/month (depending on usage)
- **Total**: ~$35-55/month

### **Cost Optimization Tips:**
1. **Use preemptible instances** for development
2. **Set up automatic shutdown** for non-production hours
3. **Use Cloud SQL's automatic backups** instead of manual backups
4. **Monitor storage usage** and clean up old files
5. **Use Cloud CDN** for static assets to reduce bandwidth costs

## 🔒 **Security Best Practices**

### **1. Network Security:**
- [ ] Restrict Cloud SQL access to your VM only
- [ ] Use VPC for network isolation
- [ ] Configure firewall rules properly
- [ ] Enable SSL/TLS for all connections

### **2. Access Control:**
- [ ] Use service accounts with minimal permissions
- [ ] Enable Cloud Identity and Access Management (IAM)
- [ ] Set up audit logging
- [ ] Use strong passwords and rotate them regularly

### **3. Data Protection:**
- [ ] Enable encryption at rest and in transit
- [ ] Set up automated backups
- [ ] Implement data retention policies
- [ ] Monitor for unusual access patterns

## 📈 **Monitoring and Maintenance**

### **1. Set Up Monitoring:**
```bash
# Install monitoring agent (already done by migration script)
sudo apt-get install google-cloud-ops-agent

# View logs in Google Cloud Console
# Go to: Logging > Logs Explorer
```

### **2. Set Up Alerts:**
- [ ] CPU usage alerts
- [ ] Memory usage alerts
- [ ] Disk space alerts
- [ ] Application error rate alerts
- [ ] Database connection alerts

### **3. Regular Maintenance:**
- [ ] Update system packages monthly
- [ ] Monitor and clean up log files
- [ ] Review and optimize database queries
- [ ] Update application dependencies
- [ ] Test backup and restore procedures

## 🆘 **Troubleshooting**

### **Common Issues:**

1. **Database Connection Failed:**
   ```bash
   # Check Cloud SQL instance status
   gcloud sql instances describe transparentsf-db-YYYYMMDD --project=YOUR_PROJECT_ID
   
   # Test connection from VM
   gcloud compute ssh transparentsf-app-YYYYMMDD --project=YOUR_PROJECT_ID --zone=us-central1-a \
     --command="psql 'postgresql://transparentsf@/transparentsf?host=/cloudsql/YOUR_PROJECT_ID:us-central1:transparentsf-db-YYYYMMDD'"
   ```

2. **GCS Access Denied:**
   ```bash
   # Check service account permissions
   gcloud projects get-iam-policy YOUR_PROJECT_ID
   
   # Test GCS access
   python tools/setup_gcs.py --test
   ```

3. **Application Not Starting:**
   ```bash
   # Check application logs
   sudo journalctl -u transparentsf -n 100
   
   # Check system resources
   htop
   df -h
   ```

4. **SSL Certificate Issues:**
   ```bash
   # Renew SSL certificate
   sudo certbot renew
   
   # Check certificate status
   sudo certbot certificates
   ```

## 📞 **Support and Resources**

### **Google Cloud Documentation:**
- [Cloud SQL Documentation](https://cloud.google.com/sql/docs)
- [Compute Engine Documentation](https://cloud.google.com/compute/docs)
- [Cloud Storage Documentation](https://cloud.google.com/storage/docs)
- [Cloud Monitoring Documentation](https://cloud.google.com/monitoring/docs)

### **TransparentSF Specific:**
- Check the `ai/logs/` directory for application logs
- Review the migration log files for detailed information
- Use the health check endpoints in your application

### **Getting Help:**
1. Check the troubleshooting section above
2. Review Google Cloud Console for error messages
3. Check application logs for specific errors
4. Consult Google Cloud support if needed

## 🎉 **Migration Complete!**

Once your migration is complete, you'll have:
- ✅ A scalable, managed PostgreSQL database
- ✅ Cloud-based file storage with automatic backups
- ✅ A robust VM hosting your application
- ✅ Comprehensive monitoring and logging
- ✅ SSL/TLS security
- ✅ Automated backups and maintenance

Your TransparentSF application is now running on Google Cloud Platform with enterprise-grade reliability and scalability!

