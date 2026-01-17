#!/bin/bash
# Quick script to fix feedback table permissions using psql directly

# Get database user from .env file
ENV_FILE="/opt/herald/.env"
DB_USER="herald"

if [ -f "$ENV_FILE" ]; then
    # Extract user from DATABASE_URL
    DATABASE_URL=$(grep "^DATABASE_URL=" "$ENV_FILE" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    if [[ "$DATABASE_URL" == *"@"* ]]; then
        USER_PART=$(echo "$DATABASE_URL" | sed 's|.*://||' | cut -d'@' -f1)
        DB_USER=$(echo "$USER_PART" | cut -d':' -f1)
    fi
fi

echo "Granting permissions on feedback table to user: $DB_USER"
echo ""

# Run as postgres superuser
sudo -u postgres psql -d herald <<EOF
GRANT ALL PRIVILEGES ON TABLE feedback TO $DB_USER;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO $DB_USER;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO $DB_USER;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO $DB_USER;
\q
EOF

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Permissions granted successfully!"
    echo "Restart the service: sudo systemctl restart herald"
else
    echo ""
    echo "✗ Failed to grant permissions"
    exit 1
fi
