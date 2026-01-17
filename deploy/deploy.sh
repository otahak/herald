#!/bin/bash
set -euo pipefail

# Herald Production Deployment Script
# Run as root on DigitalOcean droplet

echo "=== Herald Deployment Script ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Create herald user
if ! id "herald" &>/dev/null; then
    echo "Creating herald user..."
    useradd -r -s /bin/bash -d /opt/herald -m herald
fi

# Install system dependencies
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3.12 python3.12-venv python3-pip postgresql postgresql-contrib nginx curl

# Install uv if not present (system-wide)
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make uv available system-wide
    if [ -f "$HOME/.cargo/bin/uv" ]; then
        ln -sf "$HOME/.cargo/bin/uv" /usr/local/bin/uv || true
    fi
fi

# Create app directory
APP_DIR="/opt/herald"
mkdir -p "$APP_DIR"
chown herald:herald "$APP_DIR"

# Copy application files (assuming we're running from project root)
echo "Copying application files..."
rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.env*' \
    "$(pwd)/" "$APP_DIR/"

chown -R herald:herald "$APP_DIR"

# Set up Python environment
echo "Setting up Python environment..."
sudo -u herald bash << 'EOF'
cd /opt/herald
export PATH="/usr/local/bin:$PATH"
uv sync --frozen --no-dev
EOF

# Set up PostgreSQL
echo "Setting up PostgreSQL..."
DB_PASSWORD="postgres"
sudo -u postgres psql << EOF
CREATE DATABASE herald;
ALTER USER postgres WITH PASSWORD '$DB_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE herald TO postgres;
\c herald
GRANT ALL ON SCHEMA public TO postgres;
EOF

echo "Database password set for postgres user"
echo "Saving database config to .env..."

# Create .env file with generated password
cat > "$APP_DIR/.env" << EOF
DATABASE_URL=postgresql+asyncpg://postgres:${DB_PASSWORD}@localhost:5432/herald
APP_DEBUG=false
EOF
chown herald:herald "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

# Install systemd service
cp "$APP_DIR/deploy/herald.service" /etc/systemd/system/herald.service
systemctl daemon-reload
systemctl enable herald

# Configure nginx
echo "Configuring nginx..."
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/herald
ln -sf /etc/nginx/sites-available/herald /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# Initialize database schema
echo "Initializing database schema..."
sudo -u herald bash << EOF
cd /opt/herald
export DATABASE_URL="postgresql+asyncpg://postgres:${DB_PASSWORD}@localhost:5432/herald"
python3 deploy/init_db.py
EOF

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Database password (saved in /opt/herald/.env): $DB_PASSWORD"
echo ""
echo "Starting service..."
systemctl start herald
sleep 2
systemctl status herald --no-pager
echo ""
echo "Service should be running. Check logs with: sudo journalctl -u herald -f"
echo ""
