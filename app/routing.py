from fastapi import FastAPI

from app.routers.health_router import router as health_router
from app.routers.auth.email_login_router import router as email_login_router
from app.routers.auth.google_signin_router import router as google_auth_router
from app.routers.auth.phone_auth_router import router as phone_auth_router
from app.routers.more_about_user.more_about_user_router import router as more_about_user_router
from app.routers.reference.reference_router import router as reference_router
from app.services.auth.firebase_auth.auth_errors import AuthError, auth_error_handler



def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(email_login_router)
    app.include_router(google_auth_router)
    app.include_router(phone_auth_router)
    app.include_router(more_about_user_router)
    app.include_router(reference_router)
    app.add_exception_handler(AuthError, auth_error_handler)
