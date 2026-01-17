#!/bin/bash
# Fix all database permissions - set ownership and grant privileges

sudo -u postgres psql -d herald <<EOF
-- Change ownership of all tables to postgres
DO \$\$ 
DECLARE r RECORD; 
BEGIN 
  FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') 
  LOOP 
    EXECUTE 'ALTER TABLE public.' || quote_ident(r.tablename) || ' OWNER TO postgres'; 
  END LOOP; 
END \$\$;

-- Change ownership of all sequences to postgres
DO \$\$ 
DECLARE r RECORD; 
BEGIN 
  FOR r IN (SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public') 
  LOOP 
    EXECUTE 'ALTER SEQUENCE public.' || quote_ident(r.sequence_name) || ' OWNER TO postgres'; 
  END LOOP; 
END \$\$;

-- Grant all privileges on all tables
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;

-- Grant all privileges on all sequences
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;

-- Set default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO postgres;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO postgres;

-- Verify
SELECT 'Tables:' as info;
SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
EOF

echo ""
echo "✓ Permissions fixed. Restart service: sudo systemctl restart herald"
