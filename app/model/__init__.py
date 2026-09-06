from app.model.base import Base
from app.model import models  # noqa: F401  (registers all tables on Base.metadata)

__all__ = ["Base"]
