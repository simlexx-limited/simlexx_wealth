from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.model.mod_enum import CurrencyType, ProductType


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_name: str
    product_name: str
    product_type: ProductType
    description: str
    minimum_investment: Optional[Decimal] = None
    currency: CurrencyType
