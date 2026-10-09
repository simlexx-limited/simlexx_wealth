from typing import Optional

from pydantic import BaseModel, Field


class RecommendationExplanation(BaseModel):
    reason: str = Field(description="How the minimum amount was reached, from the figures given")
    alternative_reason: Optional[str] = Field(
        default=None, description="What the suggested-pace option means, only when it is shown"
    )
