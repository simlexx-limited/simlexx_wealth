from fastapi import Depends
from fastapi.routing import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import UserRole
from app.schema.product_schema import ProductResponse
from app.services.auth.authorization import require_roles
from app.services.auth.current_user import get_current_user
from app.services.products.product_service import ProductService

router = APIRouter(
    prefix="/api/v1/products",
    tags=["Products"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[ProductResponse])
async def list_products(
    token_payload: dict = Depends(require_roles(UserRole.CLIENT, UserRole.AGENT, UserRole.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    """Active products a client can choose for a goal. Clients, agents and admins only."""
    return await ProductService(session).list_active()
