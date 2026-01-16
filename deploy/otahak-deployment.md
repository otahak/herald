# Herald Deployment to otahak.com/herald

Bespoke deployment instructions for your existing DigitalOcean droplet.

## Current Server State

- **Nginx**: Config in `/etc/nginx/conf.d/server.conf`
- **otahak.com**: Serves static files from `/var/www/otahak/dist` on HTTPS (443)
- **SSL**: Let's Encrypt cert already configured
- **Python**: 3.10.12 available
- **PostgreSQL**: NOT installed (will install)
- **Ports**: 80, 443 in use; 8000 available for app
- **Resources**: 2GB RAM, 4.8GB disk free

## Deployment Steps

### 1. Install PostgreSQL

```bash
sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### 2. Create Database and User

```bash
sudo -u postgres psql << 'EOF'
CREATE DATABASE herald;
CREATE USER herald WITH PASSWORD 'GENERATE_SECURE_PASSWORD_HERE';
GRANT ALL PRIVILEGES ON DATABASE herald TO herald;
\c herald
GRANT ALL ON SCHEMA public TO herald;
\q
EOF
```

**Save the password** - you'll need it for the .env file.

### 3. Create Herald User and Directory

```bash
sudo useradd -r -s /bin/bash -d /opt/herald -m herald
sudo mkdir -p /opt/herald
sudo chown herald:herald /opt/herald
```

### 4. Install Python 3.12

The server has Python 3.10, but Herald requires 3.12+. Install Python 3.12:

```bash
sudo apt-get install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
```

### 5. Install uv (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo ln -sf $HOME/.cargo/bin/uv /usr/local/bin/uv
```

### 6. Deploy Application Code

From your local machine:

```bash
# Create deployment archive (exclude .git, .venv, etc.)
tar --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.env*' \
    -czf herald-deploy.tar.gz .

# Upload to server
scp herald-deploy.tar.gz otahak:/tmp/
```

On the server:

```bash
cd /tmp
tar -xzf herald-deploy.tar.gz -C /opt/herald/
sudo chown -R herald:herald /opt/herald
```

### 7. Install Python Dependencies

```bash
sudo -u herald bash
cd /opt/herald
export PATH="/usr/local/bin:$PATH"
# Ensure uv uses Python 3.12
uv python install 3.12
uv sync --frozen --no-dev --python 3.12
exit
```

### 8. Create Environment File

```bash
sudo -u herald cat > /opt/herald/.env << EOF
DATABASE_URL=postgresql+asyncpg://herald:YOUR_PASSWORD_HERE@localhost:5432/herald
APP_DEBUG=false
EOF
sudo chmod 600 /opt/herald/.env
```

Replace `YOUR_PASSWORD_HERE` with the password from step 2.

### 9. Initialize Database Schema

```bash
sudo -u herald bash
cd /opt/herald
export DATABASE_URL="postgresql+asyncpg://herald:YOUR_PASSWORD_HERE@localhost:5432/herald"
python3 deploy/init_db.py
exit
```

### 10. Install Systemd Service

```bash
# Update service file with database password
sudo sed "s|CHANGE_ME|YOUR_PASSWORD_HERE|g" /opt/herald/deploy/herald.service > /tmp/herald.service
sudo mv /tmp/herald.service /etc/systemd/system/herald.service

sudo systemctl daemon-reload
sudo systemctl enable herald
sudo systemctl start herald
sudo systemctl status herald
```

### 10. Add Nginx Location Block

**IMPORTANT**: Backup the config first!

```bash
sudo cp /etc/nginx/conf.d/server.conf /etc/nginx/conf.d/server.conf.backup
```

Now edit `/etc/nginx/conf.d/server.conf` and find the `otahak.com` server block (the one with `listen 443 ssl`). Add these location blocks **inside** that server block, **before** the existing `location /` block:

```nginx
    # Herald app - redirect /herald to /herald/
    location = /herald {
        return 301 /herald/;
    }
    
    # Herald app - must come BEFORE location /
    location /herald/ {
        # Trailing slash in proxy_pass strips /herald prefix
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        
        # Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts for WebSocket
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
        
        # Client body size
        client_max_body_size 10M;
    }
```

**Note**: The trailing slash in `proxy_pass http://127.0.0.1:8000/;` tells nginx to strip the `/herald` prefix before forwarding. So `/herald/api/games` becomes `/api/games` at the backend. The app is configured with `--root-path /herald` so it generates correct URLs (with `/herald` prefix) for redirects and links.

The server block should look like:

```nginx
server {
    server_name otahak.com www.otahak.com;
    client_max_body_size 15M;
    
    # Herald app
    location /herald {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
        client_max_body_size 10M;
    }
    
    location / {
        root   /var/www/otahak/dist;
        index  index.html;
    }

    listen [::]:443 ssl;
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/churchoftheimmaculateconfection.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/churchoftheimmaculateconfection.org/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}
```

### 12. Test and Reload Nginx

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 12. Verify Service Configuration

The systemd service file already includes `--root-path /herald` which tells uvicorn/Litestar that the app is mounted at `/herald`. This ensures redirects and URL generation include the `/herald` prefix.

No additional configuration needed - the service file is already correct.

## Verification

- Check service: `sudo systemctl status herald`
- Check logs: `sudo journalctl -u herald -f`
- Test URL: `https://otahak.com/herald` (should show lobby)
- Test WebSocket: Create a game, check browser console for WebSocket connection
- Test API: `curl https://otahak.com/herald/api/games` (should return JSON or redirect)

## Important Notes

1. **App mounted at `/herald`**: Nginx proxies `/herald/*` to the app, and the app is configured with `--root-path /herald` so it knows it's at a subpath. All URLs work as normal - no frontend changes needed.

2. **Database password**: Make sure to use the same password in:
   - `/opt/herald/.env`
   - `/etc/systemd/system/herald.service`

3. **Python version**: Server has Python 3.10, but app requires 3.12+. Step 4 above installs Python 3.12 from deadsnakes PPA. The systemd service uses `/opt/herald/.venv/bin/uvicorn` which will use the correct Python version from the venv.

4. **Port**: App runs on `127.0.0.1:8000` (localhost only, nginx proxies)

5. **Static files**: Currently served by the app. If you want nginx to serve them directly for better performance, add:
   ```nginx
   location /herald/static/ {
       alias /opt/herald/app/static/;
       expires 30d;
   }
   ```

## Troubleshooting

- **502 Bad Gateway**: Check if herald service is running
- **404 on /herald**: Verify nginx location block is correct and before `location /`
- **WebSocket fails**: Check `X-Script-Name` header is set
- **Database errors**: Verify password in `.env` matches PostgreSQL user
