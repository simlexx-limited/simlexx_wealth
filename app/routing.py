from fastapi import FastAPI

from app.routers.health_router import router as health_router
from app.routers.auth.email_login_router import router as email_login_router
from app.routers.auth.google_signin_router import router as google_auth_router
from app.routers.auth.phone_auth_router import router as phone_auth_router
from app.routers.auth.password_router import router as password_router
from app.routers.more_about_user.more_about_user_router import router as more_about_user_router
from app.routers.reference.reference_router import router as reference_router
from app.routers.simlexx.admin_router import router as simlexx_admin_router
from app.routers.reviews.review_router import router as review_router
from app.routers.onboarding.user_goal import router as user_goal_router
from app.routers.user_account.user_account_router import router as user_account_router
from app.routers.products.product_router import router as product_router
from app.routers.transaction.deposit.deposit_router import router as deposit_router
from app.routers.transaction.withdraw.withdraw_router import router as withdraw_router
from app.routers.dashboard.dashboard_router import router as dashboard_router
from app.services.auth.firebase_auth.auth_errors import AuthError, auth_error_handler



def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(email_login_router)
    app.include_router(google_auth_router)
    app.include_router(phone_auth_router)
    app.include_router(password_router)
    app.include_router(more_about_user_router)
    app.include_router(reference_router)
    app.include_router(simlexx_admin_router)
    app.include_router(review_router)
    app.include_router(user_goal_router)
    app.include_router(user_account_router)
    app.include_router(product_router)
    app.include_router(deposit_router)
    app.include_router(withdraw_router)
    app.include_router(dashboard_router)
    app.add_exception_handler(AuthError, auth_error_handler)
