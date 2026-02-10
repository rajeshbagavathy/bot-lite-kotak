# XTS Bot Lite - EC2 Deployment Guide

Complete step-by-step guide to deploy the XTS straddle bot on AWS EC2.

## Prerequisites

- AWS EC2 instance (Ubuntu 20.04 LTS or newer)
- XTS 5Paisa API credentials
- SSH access to EC2 instance
- IAM role with SSM Parameter Store access (for credential management)

## Step 1: EC2 Setup

### 1.1 Launch Instance

```bash
# Ubuntu 20.04 LTS, t2.micro (free tier eligible)
# Security group: Allow SSH (22), HTTP (80), HTTPS (443), Custom TCP (8001)
```

### 1.2 Connect to Instance

```bash
ssh -i "your-key.pem" ubuntu@<public-ip>
```

### 1.3 System Updates

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3-pip git
```

## Step 2: Clone xts-bot-lite

```bash
cd /opt
sudo mkdir -p xts-bot
sudo chown ubuntu:ubuntu xts-bot
cd xts-bot

# Option A: Clone from Git (if you have a repo)
git clone <your-repo-url> .

# Option B: Manual copy
# Upload xts-bot-lite folder via SCP or S3
scp -i "key.pem" -r xts-bot-lite ubuntu@<ip>:/opt/xts-bot
```

## Step 3: Install Dependencies

```bash
cd /opt/xts-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

## Step 4: Configure Credentials

### Option A: AWS SSM Parameter Store (Recommended)

1. **Create IAM role for EC2** (if not already done):

```bash
# Via AWS Console or CLI:
aws iam create-role \
  --role-name xts-bot-role \
  --assume-role-service-trust-policy-document file://trust-policy.json

# trust-policy.json:
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

2. **Attach SSM policy**:

```bash
aws iam attach-role-policy \
  --role-name xts-bot-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMReadOnlyAccess
```

3. **Create instance profile**:

```bash
aws ec2 associate-iam-instance-profile \
  --iam-instance-profile Name=xts-bot-profile \
  --instance-id <your-instance-id>
```

4. **Store credentials in SSM Parameter Store**:

```bash
aws ssm put-parameter --name apikey --value "YOUR_API_KEY" --type String
aws ssm put-parameter --name apisecret --value "YOUR_API_SECRET" --type String --with-decryption
aws ssm put-parameter --name clientid --value "YOUR_CLIENT_ID" --type String
aws ssm put-parameter --name marketdataapikey --value "YOUR_MARKET_API_KEY" --type String
aws ssm put-parameter --name marketdataapisecret --value "YOUR_MARKET_API_SECRET" --type String --with-decryption
aws ssm put-parameter --name loginusername --value "YOUR_LOGIN_USER" --type String
aws ssm put-parameter --name loginpassword --value "YOUR_LOGIN_PASSWORD" --type String --with-decryption
```

### Option B: Environment Variables

Create a file `/opt/xts-bot/.env`:

```bash
export XTS_API_KEY_5P="YOUR_API_KEY"
export XTS_API_SECRET_5P="YOUR_API_SECRET"
export XTS_5P_CLIENTID_5P="YOUR_CLIENT_ID"
export XTS_MARKET_API_KEY_5P="YOUR_MARKET_API_KEY"
export XTS_MARKET_API_SECRET_5P="YOUR_MARKET_API_SECRET"
export LOGIN_USERNAME_5P="YOUR_LOGIN_USER"
export LOGIN_PASSWORD_5P="YOUR_LOGIN_PASSWORD"
export BASIC_AUTH_USERNAME="dashboard_user"
export BASIC_AUTH_PASSWORD="dashboard_password"
```

Load the environment before running:

```bash
source /opt/xts-bot/.env
python3 bot.py
```

## Step 5: Create systemd Service (Recommended)

Create `/etc/systemd/system/xts-bot.service`:

```ini
[Unit]
Description=XTS Straddle Trading Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/xts-bot
ExecStart=/opt/xts-bot/venv/bin/python3 /opt/xts-bot/bot.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/xts-bot.log
StandardError=append:/var/log/xts-bot.log
Environment="PATH=/opt/xts-bot/venv/bin"

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable xts-bot
sudo systemctl start xts-bot
```

Check status:

```bash
sudo systemctl status xts-bot
```

View logs:

```bash
sudo journalctl -u xts-bot -f
# or
tail -f /var/log/xts-bot.log
```

## Step 6: Configure Firewall

```bash
# Allow port 8001 for the web UI
sudo ufw allow 22/tcp
sudo ufw allow 8001/tcp
sudo ufw enable
```

Or via AWS Security Group:
- Allow TCP 22 (SSH) from your IP
- Allow TCP 8001 (Web UI) from your IP or 0.0.0.0/0 (be cautious)

## Step 7: Web Server Setup (Optional)

To access the bot UI via HTTPS and a custom domain, set up Nginx as a reverse proxy:

### Install Nginx

```bash
sudo apt install -y nginx
```

### Create Nginx config

Create `/etc/nginx/sites-available/xts-bot`:

```nginx
upstream xts_bot {
    server 127.0.0.1:8001;
}

server {
    listen 80;
    server_name trading.example.com;

    location / {
        proxy_pass http://xts_bot;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Enable the site

```bash
sudo ln -s /etc/nginx/sites-available/xts-bot /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Add SSL (Let's Encrypt)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d trading.example.com
```

## Step 8: Test the Deployment

### 1. Check bot status

```bash
sudo systemctl status xts-bot
```

### 2. Check logs in real-time

```bash
sudo journalctl -u xts-bot -f
```

### 3. Access the web UI

```
http://<ec2-public-ip>:8001
# or
https://trading.example.com
```

Use BasicAuth credentials configured in Step 4.

### 4. Verify orders place at scheduled times

- Monitor the dashboard at 09:20, 10:01, 12:40, 13:50 (IST)
- Check XTS broking terminal to confirm orders

## Step 9: Monitoring & Maintenance

### Health Check Script

Create `/opt/xts-bot/health-check.sh`:

```bash
#!/bin/bash
status=$(curl -s -u username:password http://localhost:8001/state | jq '.portfolio.mtm')
if [ $? -ne 0 ]; then
    echo "Bot UI unreachable"
    systemctl restart xts-bot
else
    echo "Bot healthy. Current MTM: $status"
fi
```

Add to crontab:

```bash
crontab -e
# Add: */5 * * * * /opt/xts-bot/health-check.sh
```

### Log Rotation

Create `/etc/logrotate.d/xts-bot`:

```
/var/log/xts-bot.log {
    rotate 7
    daily
    missingok
    notifempty
    compress
    delaycompress
    postrotate
        systemctl reload xts-bot > /dev/null 2>&1 || true
    endscript
}
```

## Step 10: Backup Strategy

```bash
# Daily backup of config (if using .env file)
0 0 * * * tar -czf /backups/xts-bot-backup-$(date +\%Y\%m\%d).tar.gz /opt/xts-bot
```

## Troubleshooting

### Bot won't start

```bash
# Check Python syntax
python3 -m py_compile bot.py

# Check logs
journalctl -u xts-bot --no-pager | tail -50

# Run manually to see errors
cd /opt/xts-bot
source venv/bin/activate
python3 bot.py
```

### Orders not executing

1. Verify IAM instance has SSM access
2. Check that parameters exist in SSM Parameter Store
3. Confirm system time matches market hours (IST)
4. Review bot.log for API errors

### MTM calculation errors

1. Confirm XTS market data session is active
2. Check that positions are being fetched correctly
3. Verify LTP subscriptions are working

### Web UI unavailable

```bash
# Check if flask is listening
sudo lsof -i :8001

# Restart the service
sudo systemctl restart xts-bot

# Check firewall
sudo ufw status
```

## Rollback Plan

If the bot behaves unexpectedly:

```bash
# Stop the bot
sudo systemctl stop xts-bot

# Restore previous version
git checkout <previous-commit>
# or
tar -xzf /backups/xts-bot-backup-<date>.tar.gz -C /

# Verify the fix
systemctl start xts-bot
```

## Security Best Practices

1. **Use IAM roles** instead of hardcoded credentials
2. **Enable SSL/TLS** for web UI access
3. **Restrict SSH** to your IP only
4. **Rotate credentials** regularly
5. **Use strong BasicAuth** passwords for the UI
6. **Enable AWS CloudTrail** to audit API access
7. **Set EC2 instance profile** to limit AWS permissions
8. **Disable password login** on EC2, use SSH keys only

## Scaling Considerations

For multiple trading accounts:

1. Run multiple bot instances on different ports (8001, 8002, ...)
2. Use different AWS regions for geographic redundancy
3. Set up health checks and auto-recovery via CloudWatch
4. Store logs in CloudWatch Logs for centralized monitoring

## Contact & Support

- XTS API Docs: https://xts.5paisa.com/docs/
- AWS Support: https://console.aws.amazon.com/support
- Bot Issues: Review bot.log and README.md
