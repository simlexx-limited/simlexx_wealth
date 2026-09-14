from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.middleware.cors_middleware import add_cors_middleware
from app.routing import register_routers
from app.services.rate_limiter.limiter import limiter, rate_limit_exceeded_handler
from app.startup import lifespan

app = FastAPI(
    title="Simlexx Wealth Creation",
    version="0.0.1",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

add_cors_middleware(app)
register_routers(app)
