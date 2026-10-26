# db.py
import firebase_admin
from firebase_admin import firestore as fs
import logging

logger = logging.getLogger(__name__)

_db = None

def init_db():
    """Initialize Firestore client"""
    global _db
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        _db = fs.client()
        logger.info("Firestore client initialized")
        return _db
    except Exception as e:
        logger.error(f"Failed to initialize Firestore: {e}")
        return None

def get_db():
    """Get Firestore client"""
    if _db is None:
        init_db()
    if _db is None:
        raise Exception("Firestore client not available")
    return _db

def check_db_health():
    """Check if Firestore connection is healthy"""
    try:
        db = get_db()
        list(db.collection('_health').limit(1).stream())
        return True
    except Exception as e:
        logger.error(f"Firestore health check failed: {e}")
        return False
