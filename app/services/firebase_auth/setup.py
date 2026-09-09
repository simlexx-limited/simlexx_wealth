import logging
import firebase_admin
from firebase_admin import auth, credentials
from pathlib import Path

from functools import lru_cache

from app.config.setting import get_settings

logger = logging.getLogger(__name__)
setting = get_settings()

path_to_json = Path(setting.PATH_TO_FIREBASE)

@lru_cache
def init_firebase():
    """
    Initialise firebase
    """
    if not firebase_admin._apps:
        logger.info("Connecting to Firebase...")
        firebase_credentials = credentials.Certificate(path_to_json)
        firebase_admin.initialize_app(firebase_credentials)
        logger.info("Firebase connection established")
        logger.info("Success!")
    else:
        logger.info("Firebase already initialized, skipping")


def check_firebase() -> None:
    """
    Lightweight live round-trip to Firebase, for use in health checks.
    Raises if the Admin SDK can't actually reach Firebase right now.
    """
    auth.list_users(max_results=1)
