# Herald Production Deployment

Deployment guide for DigitalOcean droplet (2GB RAM, 1 vCPU).

## Prerequisites

- Fresh Ubuntu 22.04+ droplet
- Root access
- Domain name (optional, for SSL)

## Quick Deploy

1. **On your local machine**, prepare and upload:

```bash
# From project root
tar --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    -czf herald-deploy.tar.gz .

# Upload to droplet
scp herald-deploy.tar.gz root@your-droplet-ip:/tmp/
```

2. **On the droplet**, extract and run:

```bash
cd /tmp
tar -xzf herald-deploy.tar.gz
cd herald
chmod +x deploy/deploy.sh
sudo ./deploy/deploy.sh
```

3. **Update passwords**:

```bash
# Generate a secure password
openssl rand -base64 32

# Update in both files:
sudo nano /opt/herald/.env
sudo nano /etc/systemd/system/herald.service

# Restart service
sudo systemctl restart herald
```

## Manual Setup

### 1. System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv postgresql postgresql-contrib nginx curl
```

### 2. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.cargo/bin:$PATH"
```

### 3. Create User & Directory

```bash
sudo useradd -r -s /bin/bash -d /opt/herald -m herald
sudo mkdir -p /opt/herald
sudo chown herald:herald /opt/herald
```

### 4. Deploy Application

```bash
# Copy files to /opt/herald (exclude .git, .venv, etc.)
sudo rsync -av --exclude='.git' --exclude='.venv' /path/to/herald/ /opt/herald/
sudo chown -R herald:herald /opt/herald
```

### 5. Install Python Dependencies

```bash
sudo -u herald bash
cd /opt/herald
export PATH="$HOME/.cargo/bin:$PATH"
uv sync --frozen --no-dev
exit
```

### 6. PostgreSQL Setup

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE herald;
ALTER USER postgres WITH PASSWORD 'your-secure-password';
GRANT ALL PRIVILEGES ON DATABASE herald TO postgres;
\c herald
GRANT ALL ON SCHEMA public TO postgres;
\q
```

### 7. Environment Configuration

```bash
sudo -u herald cat > /opt/herald/.env << EOF
DATABASE_URL=postgresql+asyncpg://postgres:your-secure-password@localhost:5432/herald
APP_DEBUG=false
EOF
sudo chmod 600 /opt/herald/.env
```

### 7.5. Initialize Database Schema

```bash
sudo -u herald bash
cd /opt/herald
export DATABASE_URL="postgresql+asyncpg://postgres:your-secure-password@localhost:5432/herald"
python3 deploy/init_db.py
exit
```

### 8. Systemd Service

```bash
sudo cp deploy/herald.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable herald
sudo systemctl start herald
sudo systemctl status herald
```

### 9. Nginx Configuration

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/herald
sudo ln -s /etc/nginx/sites-available/herald /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## SSL with Let's Encrypt (Optional)

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## Maintenance

### View Logs

```bash
sudo journalctl -u herald -f
sudo tail -f /var/log/nginx/error.log
```

### Restart Service

```bash
sudo systemctl restart herald
```

### Update Application

```bash
# Pull latest code
cd /opt/herald
sudo -u herald git pull  # if using git
# Or rsync new files

# Reinstall dependencies if needed
sudo -u herald uv sync --frozen --no-dev

# Restart
sudo systemctl restart herald
```

## Resource Monitoring

For a 2GB/1vCPU droplet:
- Monitor with: `htop`, `free -h`, `df -h`
- PostgreSQL: `sudo -u postgres psql -c "SELECT * FROM pg_stat_activity;"`
- App workers: 2 (configured in service file)

## Troubleshooting

- **Service won't start**: Check logs with `sudo journalctl -u herald -n 50`
- **Database connection errors**: Verify DATABASE_URL in .env matches PostgreSQL user/password
- **502 Bad Gateway**: Check if herald service is running: `sudo systemctl status herald`
- **WebSocket issues**: Verify nginx proxy headers are set correctly
