#!/bin/bash
# Fix .env file format by removing leading spaces
# This script can be run on the server to fix the .env file without a full deployment

ENV_FILE="/opt/herald/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found at $ENV_FILE"
    exit 1
fi

echo "Fixing .env file format (removing leading spaces)..."
# Create a temporary file with fixed format
sed 's/^[[:space:]]*//' "$ENV_FILE" > "$ENV_FILE.tmp"

# Verify the fix worked
if grep -q "^[[:space:]]" "$ENV_FILE.tmp"; then
    echo "ERROR: Still has leading spaces after fix attempt"
    rm "$ENV_FILE.tmp"
    exit 1
fi

# Backup original
cp "$ENV_FILE" "$ENV_FILE.backup.$(date +%Y%m%d_%H%M%S)"

# Replace with fixed version
mv "$ENV_FILE.tmp" "$ENV_FILE"
chown herald:herald "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "✓ .env file fixed (removed leading spaces)"
echo "✓ Backup created: $ENV_FILE.backup.*"
echo ""
echo "Verifying key variables are present..."
if grep -q "^GOOGLE_CLIENT_ID=" "$ENV_FILE"; then
    echo "✓ GOOGLE_CLIENT_ID found"
else
    echo "✗ GOOGLE_CLIENT_ID not found"
fi

if grep -q "^GOOGLE_CLIENT_SECRET=" "$ENV_FILE"; then
    echo "✓ GOOGLE_CLIENT_SECRET found"
else
    echo "✗ GOOGLE_CLIENT_SECRET not found"
fi

echo ""
echo "To apply changes, restart the service:"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl restart herald"
