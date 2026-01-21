"""
Database connection management for the evidence analysis system.

This module provides PostgreSQL connection pooling and management utilities.
Supports both connection strings and individual parameters from environment variables.
"""

import os
import logging
from typing import Optional
from contextlib import contextmanager
from dotenv import load_dotenv
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Manages PostgreSQL database connections with connection pooling."""
    
    _instance = None
    _connection_pool: Optional[pool.SimpleConnectionPool] = None
    
    def __new__(cls):
        """Singleton pattern to ensure only one connection pool exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the database connection pool if not already initialized."""
        if self._connection_pool is None:
            self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize the connection pool using environment variables."""
        try:
            # Try DATABASE_URL first (Heroku/Railway style)
            database_url = os.getenv('DATABASE_URL')
            
            if database_url:
                logger.info("Initializing database connection pool from DATABASE_URL")
                self._connection_pool = pool.SimpleConnectionPool(
                    minconn=1,
                    maxconn=10,
                    dsn=database_url
                )
            else:
                # Fall back to individual parameters
                logger.info("Initializing database connection pool from individual parameters")
                self._connection_pool = pool.SimpleConnectionPool(
                    minconn=1,
                    maxconn=10,
                    host=os.getenv('DB_HOST', 'localhost'),
                    port=int(os.getenv('DB_PORT', '5432')),
                    database=os.getenv('DB_NAME', 'evidence_analysis'),
                    user=os.getenv('DB_USER', 'postgres'),
                    password=os.getenv('DB_PASSWORD', '')
                )
            
            logger.info("Database connection pool initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database connection pool: {e}")
            raise
    
    @contextmanager
    def get_connection(self):
        """
        Context manager to get a database connection from the pool.
        
        Usage:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM states")
                results = cursor.fetchall()
        
        Yields:
            psycopg2.connection: Database connection object
        """
        conn = None
        try:
            conn = self._connection_pool.getconn()
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                self._connection_pool.putconn(conn)
    
    @contextmanager
    def get_cursor(self, cursor_factory=RealDictCursor):
        """
        Context manager to get a cursor with automatic connection management.
        
        Args:
            cursor_factory: Cursor type (default: RealDictCursor for dict-like rows)
        
        Usage:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT * FROM states WHERE state_code = %s", ('HR',))
                result = cursor.fetchone()
                # result is a dict like {'id': 1, 'state_code': 'HR', ...}
        
        Yields:
            psycopg2.cursor: Database cursor object
        """
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=cursor_factory)
            try:
                yield cursor
            finally:
                cursor.close()
    
    def execute_query(self, query: str, params: tuple = None, fetch_one: bool = False):
        """
        Execute a query and return results.
        
        Args:
            query: SQL query string
            params: Query parameters tuple
            fetch_one: If True, returns single row; if False, returns all rows
        
        Returns:
            Single row (dict) if fetch_one=True, otherwise list of dicts
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params)
            if fetch_one:
                return cursor.fetchone()
            return cursor.fetchall()
    
    def execute_insert(self, query: str, params: tuple = None, return_id: bool = True):
        """
        Execute an INSERT query and optionally return the inserted ID.
        
        Args:
            query: SQL INSERT query string
            params: Query parameters tuple
            return_id: If True, returns the inserted row's ID
        
        Returns:
            Integer ID of inserted row if return_id=True, otherwise None
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params)
            if return_id:
                cursor.execute("SELECT lastval()")
                return cursor.fetchone()['lastval']
    
    def execute_update(self, query: str, params: tuple = None):
        """
        Execute an UPDATE query.
        
        Args:
            query: SQL UPDATE query string
            params: Query parameters tuple
        
        Returns:
            Number of rows affected
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.rowcount
    
    def execute_delete(self, query: str, params: tuple = None):
        """
        Execute a DELETE query.
        
        Args:
            query: SQL DELETE query string
            params: Query parameters tuple
        
        Returns:
            Number of rows deleted
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.rowcount
    
    def test_connection(self) -> bool:
        """
        Test if the database connection is working.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False
    
    def close_pool(self):
        """Close all connections in the pool. Call this on application shutdown."""
        if self._connection_pool:
            self._connection_pool.closeall()
            logger.info("Database connection pool closed")


# Global database connection instance
db = DatabaseConnection()


# Convenience functions for direct access
def get_connection():
    """Get a database connection from the pool."""
    return db.get_connection()


def get_cursor(cursor_factory=RealDictCursor):
    """Get a database cursor."""
    return db.get_cursor(cursor_factory)


def execute_query(query: str, params: tuple = None, fetch_one: bool = False):
    """Execute a query and return results."""
    return db.execute_query(query, params, fetch_one)


def test_connection() -> bool:
    """Test database connection."""
    return db.test_connection()


# Example usage
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    
    # Test connection
    if test_connection():
        print("✓ Database connection successful!")
        
        # Example query
        with get_cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()
            print(f"PostgreSQL version: {version}")
    else:
        print("✗ Database connection failed!")
