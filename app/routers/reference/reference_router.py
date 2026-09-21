from fastapi.routing import APIRouter
from pydantic import BaseModel

from app.services.reference.country_service import list_countries

router = APIRouter(prefix="/api/v1/reference", tags=["Reference"])


class CountryOut(BaseModel):
    code: str
    name: str
    served: bool


@router.get("/countries", response_model=list[CountryOut])
async def get_countries():
    """
    All countries (for dropdowns). `served` marks where Simlexx operates.
    """
    return list_countries()
