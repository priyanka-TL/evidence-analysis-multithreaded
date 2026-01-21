#!/bin/bash

# Database Setup Script for Evidence Analysis System
# This script helps set up the PostgreSQL database

echo "=========================================="
echo "Evidence Analysis Database Setup"
echo "=========================================="
echo ""

# Check if PostgreSQL is running
if ! pg_isready -q; then
    echo "❌ PostgreSQL is not running!"
    echo "Please start PostgreSQL first:"
    echo "  sudo systemctl start postgresql"
    echo "  OR"
    echo "  sudo service postgresql start"
    exit 1
fi

echo "✓ PostgreSQL is running"
echo ""

# Prompt for database details
echo "Please enter your PostgreSQL credentials:"
read -p "Username [default: dell]: " DB_USER
DB_USER=${DB_USER:-dell}

read -sp "Password (press Enter if no password): " DB_PASSWORD
echo ""

read -p "Host [default: localhost]: " DB_HOST
DB_HOST=${DB_HOST:-localhost}

read -p "Port [default: 5432]: " DB_PORT
DB_PORT=${DB_PORT:-5432}

read -p "Database name [default: evidence_analysis]: " DB_NAME
DB_NAME=${DB_NAME:-evidence_analysis}

# Build connection string
if [ -z "$DB_PASSWORD" ]; then
    CONNECTION_STRING="postgresql://${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
else
    CONNECTION_STRING="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

echo ""
echo "=========================================="
echo "Database Configuration"
echo "=========================================="
echo "Connection String: postgresql://${DB_USER}:***@${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo ""

# Try to create database as current user
echo "Creating database '${DB_NAME}'..."
createdb "${DB_NAME}" 2>/dev/null

if [ $? -eq 0 ]; then
    echo "✓ Database created successfully"
else
    echo "⚠ Database might already exist or you need superuser privileges"
    echo ""
    echo "If database doesn't exist, run this command manually:"
    echo "  sudo -u postgres createdb ${DB_NAME}"
    echo "  sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};\""
    echo ""
    read -p "Continue with migrations? (y/n): " CONTINUE
    if [ "$CONTINUE" != "y" ]; then
        exit 1
    fi
fi

echo ""
echo "=========================================="
echo "Running Database Migrations"
echo "=========================================="

# Run schema migration
echo "Running schema migration..."
psql "${CONNECTION_STRING}" -f database/migrations/001_create_schema.sql

if [ $? -eq 0 ]; then
    echo "✓ Schema created successfully"
else
    echo "❌ Schema migration failed"
    exit 1
fi

echo ""
# Run seed data migration
echo "Running seed data migration..."
psql "${CONNECTION_STRING}" -f database/migrations/002_seed_data.sql

if [ $? -eq 0 ]; then
    echo "✓ Seed data loaded successfully"
else
    echo "❌ Seed data migration failed"
    exit 1
fi

echo ""
echo "=========================================="
echo "Updating .env file"
echo "=========================================="

# Update .env file
if grep -q "^DATABASE_URL=" .env; then
    # Update existing line
    sed -i "s|^DATABASE_URL=.*|DATABASE_URL=${CONNECTION_STRING}|" .env
    echo "✓ Updated DATABASE_URL in .env"
else
    # Add new line
    echo "DATABASE_URL=${CONNECTION_STRING}" >> .env
    echo "✓ Added DATABASE_URL to .env"
fi

echo ""
echo "=========================================="
echo "Testing Connection"
echo "=========================================="

# Test connection using Python
cd "$(dirname "$0")"
python3 -c "
import sys
sys.path.insert(0, '.')
from database.connection import test_connection

if test_connection():
    print('✓ Database connection successful!')
    print('')
    print('Your database is ready to use!')
else:
    print('❌ Database connection failed!')
    print('Please check your credentials and try again.')
    sys.exit(1)
"

if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "Setup Complete! 🎉"
    echo "=========================================="
    echo ""
    echo "Next steps:"
    echo "  1. Review database/README.md for usage examples"
    echo "  2. Test queries: psql ${DB_NAME} -c 'SELECT * FROM states;'"
    echo "  3. Start building business logic layer"
else
    echo ""
    echo "Setup completed with connection issues."
    echo "Please verify your DATABASE_URL in .env file."
fi
