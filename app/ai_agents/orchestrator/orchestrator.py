#!/usr/bin/env python3

from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from app.ai_agents.adk.agent_config import Agents
from app.ai_agents.estimator import estimate
from app.ai_agents.prompts.recommender import instructions, prompts
from app.ai_agents.schemas.recommendation_explanation import RecommendationExplanation

from app.model.models import Products
from app.schema.user_goal import AlternativeSavingStrategy, SavingRecommendation, UserGoalCreate

def describe_product(product:Optional[Products]) -> str:
    if product is None:
        return "none"
    minimum = (
        f"minimum investment {product.minimum_investment} {product.currency.value}"
        if product.minimum_investment is not None else "no stated minimum investment"
    )
    return (f"{product.product_name} by {product.company_name} "
            f"({product.product_type.value}), {minimum}. {product.description}")

class Orchestrator:
    def __init__(self):
        pass

    async def recommendation(self, user_id:str, user_goal:UserGoalCreate, product:Optional[Products]=None) -> SavingRecommendation:
        """
        Calculate the minimum saving for the goal, have the agent explain it,
        and return the calculated amounts with the explanation.
        """
        today = date.today()
        figures = estimate(
            target_amount=user_goal.target_amount,
            current_amount=user_goal.current_amount,
            minimum_income=user_goal.minimum_income,
            income_regularity=user_goal.income_regularity,
            target_date=user_goal.target_date,
            today=today,
        )
        filled = prompts.format(
            income_regularity=user_goal.income_regularity.value,
            minimum_income=user_goal.minimum_income,
            purpose=user_goal.purpose,
            target_amount=user_goal.target_amount,
            currency=user_goal.currency.value,
            current_amount=user_goal.current_amount,
            target_date=user_goal.target_date.isoformat(),
            today=today.isoformat(),
            product=describe_product(product),
            remaining=figures.remaining,
            periods_until_target=figures.periods_until_target,
            frequency=figures.frequency.value,
            required=figures.required,
            suggested_pace=figures.suggested_pace.quantize(Decimal("1")),
            periods_at_suggested_pace=figures.periods_at_suggested_pace,
            suggested_pace_date=figures.suggested_pace_date.isoformat(),
            show_suggested_pace="yes" if figures.show_suggested_pace else "no",
        )
        recommendation_agent = Agents(
            instructions=instructions,
            prompts=filled,
            agent_builder="recommendation_agent"
            )
        explanation = RecommendationExplanation.model_validate_json(await recommendation_agent.run(
            user_id=user_id,
            agent_name="recommendation_agent"
        ))

        alternatives = None
        if figures.show_suggested_pace and explanation.alternative_reason:
            alternatives = [AlternativeSavingStrategy(
                saving_amount_recommended=figures.suggested_pace,
                frequency=figures.frequency,
                reason=explanation.alternative_reason,
            )]
        return SavingRecommendation(
            saving_amount_recommended=figures.required,
            currency=user_goal.currency,
            frequency=figures.frequency,
            reason=explanation.reason,
            alternatives=alternatives,
        )

@lru_cache
def orchestrator()->Orchestrator:
    return Orchestrator()
