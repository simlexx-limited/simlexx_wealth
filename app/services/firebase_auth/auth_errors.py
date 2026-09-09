from fastapi import Request
from fastapi.responses import JSONResponse


class AuthError(Exception):
    """Base class for our own authentication/authorization errors.

    Raise any subclass of this from anywhere in the app (a route, a service,
    a dependency) and it will be converted into the right HTTP response
    automatically by the handler registered in app/routing.py — no
    try/except or HTTPException needed at the call site.
    """
    status_code = 400
    detail = "Authentication error"

    def __init__(self, detail: str | None = None):
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class DuplicateAccountError(AuthError):
    status_code = 409
    detail = "An account with this phone number or email already exists"


class InvalidCredentialsError(AuthError):
    status_code = 401
    detail = "Invalid credentials"


class AccountNotFoundError(AuthError):
    status_code = 404
    detail = "Account not found"


class AccountDisabledError(AuthError):
    status_code = 403
    detail = "This account has been disabled"


class UnverifiedAccountError(AuthError):
    status_code = 403
    detail = "Account not verified"

async def auth_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Starlette's exception-handler type is contravariant in `exc` (it must
    # accept any Exception to be a valid handler), even though it's only
    # ever actually invoked with AuthError here, per the registration in
    # app/routing.py: app.add_exception_handler(AuthError, auth_error_handler).
    assert isinstance(exc, AuthError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
