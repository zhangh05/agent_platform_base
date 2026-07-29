# backend/api/version.py

from backend.core.settings import (
    APP_NAME, APP_VERSION, BUILD_COMMIT, API_MODE, PRODUCT_READY,
)


def get_version():
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "build_commit": BUILD_COMMIT,
        "product_ready": PRODUCT_READY,
        "api_mode": API_MODE,
    }
