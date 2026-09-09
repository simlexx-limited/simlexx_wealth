from fastapi import FastAPI

from app.middleware.cors_middleware import add_cors_middleware
from app.routing import register_routers
from app.startup import lifespan

app = FastAPI(
    title="Simlexx Wealth Creation",
    version="0.0.1",
    lifespan=lifespan,
)

add_cors_middleware(app)
register_routers(app)
