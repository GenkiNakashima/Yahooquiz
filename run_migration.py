#!/usr/bin/env python3
"""
Database migration runner script
Usage: python run_migration.py
"""
import os
import sys
from db import execute_migration

def main():
    migration_files = [
        'migrations/001_initial_schema.sql'
    ]

    print("Starting database migration...")

    for migration_file in migration_files:
        if not os.path.exists(migration_file):
            print(f"Error: Migration file not found: {migration_file}")
            sys.exit(1)

        print(f"Executing migration: {migration_file}")
        success = execute_migration(migration_file)

        if not success:
            print(f"Failed to execute migration: {migration_file}")
            sys.exit(1)

        print(f"Successfully executed: {migration_file}")

    print("\nAll migrations completed successfully!")

if __name__ == '__main__':
    # Check if DATABASE_URL is set
    if not os.environ.get('DATABASE_URL'):
        print("Error: DATABASE_URL environment variable is not set")
        print("Please set DATABASE_URL to your PostgreSQL connection string")
        sys.exit(1)

    main()
