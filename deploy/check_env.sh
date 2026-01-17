#!/bin/bash
# Diagnostic script to check .env file and systemd environment loading

ENV_FILE="/opt/herald/.env"

echo "=== .env File Diagnostic ==="
echo ""

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ ERROR: .env file not found at $ENV_FILE"
    exit 1
fi

echo "✓ .env file exists"
echo ""

# Check for leading spaces
echo "Checking for leading spaces..."
if grep -q "^[[:space:]]" "$ENV_FILE"; then
    echo "❌ PROBLEM FOUND: .env file has lines with leading spaces!"
    echo "   This will prevent systemd from loading the variables correctly."
    echo ""
    echo "First 5 lines showing format:"
    head -5 "$ENV_FILE" | cat -A
    echo ""
    echo "To fix this, run: sudo bash /opt/herald/deploy/fix_env_format.sh"
else
    echo "✓ No leading spaces found"
fi
echo ""

# Check for required variables
echo "Checking for required variables..."
if grep -q "^GOOGLE_CLIENT_ID=" "$ENV_FILE"; then
    echo "✓ GOOGLE_CLIENT_ID found"
    VALUE=$(grep "^GOOGLE_CLIENT_ID=" "$ENV_FILE" | cut -d'=' -f2- | head -c 30)
    if [ -z "$VALUE" ] || [ "$VALUE" = "" ]; then
        echo "  ⚠ WARNING: GOOGLE_CLIENT_ID value appears to be empty"
    else
        echo "  Value starts with: ${VALUE}..."
    fi
else
    echo "❌ GOOGLE_CLIENT_ID not found"
fi

if grep -q "^GOOGLE_CLIENT_SECRET=" "$ENV_FILE"; then
    echo "✓ GOOGLE_CLIENT_SECRET found"
    VALUE=$(grep "^GOOGLE_CLIENT_SECRET=" "$ENV_FILE" | cut -d'=' -f2- | head -c 10)
    if [ -z "$VALUE" ] || [ "$VALUE" = "" ]; then
        echo "  ⚠ WARNING: GOOGLE_CLIENT_SECRET value appears to be empty"
    else
        echo "  Value starts with: ${VALUE}..."
    fi
else
    echo "❌ GOOGLE_CLIENT_SECRET not found"
fi
echo ""

# Check what systemd sees
echo "=== Systemd Environment Check ==="
SERVICE_ENV=$(systemctl show herald --property=Environment --property=EnvironmentFile 2>/dev/null || echo "")
if echo "$SERVICE_ENV" | grep -q "GOOGLE_CLIENT_ID="; then
    echo "✓ GOOGLE_CLIENT_ID is loaded in systemd environment"
else
    echo "❌ GOOGLE_CLIENT_ID NOT found in systemd environment"
    echo "   This means systemd is not loading it from the .env file"
    echo ""
    echo "EnvironmentFile setting:"
    echo "$SERVICE_ENV" | grep EnvironmentFile || echo "  Not found"
fi
echo ""

# Check service status
echo "=== Service Status ==="
systemctl is-active herald >/dev/null 2>&1 && echo "✓ Service is running" || echo "❌ Service is not running"
echo ""

echo "=== Recent Service Logs (OAuth related) ==="
journalctl -u herald -n 30 --no-pager 2>/dev/null | grep -i "oauth\|google\|environment" | tail -10 || echo "No OAuth-related logs found"
echo ""

echo "=== Recommendations ==="
if grep -q "^[[:space:]]" "$ENV_FILE"; then
    echo "1. Fix the .env file format: sudo bash /opt/herald/deploy/fix_env_format.sh"
    echo "2. Restart the service: sudo systemctl daemon-reload && sudo systemctl restart herald"
    echo "3. Check logs: sudo journalctl -u herald -n 50 | grep -i oauth"
else
    echo "If OAuth still doesn't work:"
    echo "1. Verify .env file has correct values (no quotes, no extra spaces)"
    echo "2. Restart service: sudo systemctl daemon-reload && sudo systemctl restart herald"
    echo "3. Check startup logs: sudo journalctl -u herald -n 100 | grep -A5 'Starting app'"
fi
