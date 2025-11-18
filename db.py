# db.py
import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# Database connection pool
connection_pool = None

def init_db_pool():
    """Initialize database connection pool"""
    global connection_pool

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.warning("DATABASE_URL not set, database features will be disabled")
        return None

    try:
        connection_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=database_url,
            cursor_factory=RealDictCursor
        )
        logger.info("Database connection pool initialized")
        return connection_pool
    except Exception as e:
        logger.error(f"Failed to initialize database pool: {e}")
        return None

@contextmanager
def get_db_connection():
    """
    Context manager for database connections.
    Usage:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users")
    """
    global connection_pool

    if connection_pool is None:
        init_db_pool()

    if connection_pool is None:
        raise Exception("Database connection pool not available")

    conn = None
    try:
        conn = connection_pool.getconn()
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        if conn:
            connection_pool.putconn(conn)

@contextmanager
def get_db_cursor(commit=True):
    """
    Context manager for database cursor with automatic commit/rollback.
    Usage:
        with get_db_cursor() as cur:
            cur.execute("INSERT INTO users ...")
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database transaction error: {e}")
            raise
        finally:
            cursor.close()

def execute_migration(sql_file_path):
    """Execute a SQL migration file"""
    try:
        with open(sql_file_path, 'r', encoding='utf-8') as f:
            sql = f.read()

        with get_db_cursor() as cur:
            cur.execute(sql)

        logger.info(f"Successfully executed migration: {sql_file_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to execute migration {sql_file_path}: {e}")
        return False

def check_db_health():
    """Check if database connection is healthy"""
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False

def close_db_pool():
    """Close all connections in the pool"""
    global connection_pool
    if connection_pool:
        connection_pool.closeall()
        logger.info("Database connection pool closed")
