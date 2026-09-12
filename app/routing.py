from fastapi import FastAPI

from app.routers.health_router import router as health_router
from app.routers.email_login_router import router as email_login_router
from app.routers.google_signin_router import router as google_auth_router
from app.services.firebase_auth.auth_errors import AuthError, auth_error_handler



def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(email_login_router)
    app.include_router(google_auth_router)
    app.add_exception_handler(AuthError, auth_error_handler)
