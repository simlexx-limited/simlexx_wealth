from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.ai_agents.adk.agent_config import Agents
from app.ai_agents.estimator import estimate
from app.ai_agents.orchestrator.orchestrator import Orchestrator
from app.model.mod_enum import CurrencyType, IncomeFrequency, SavingFrequency
from app.schema.user_goal import UserGoalCreate

TODAY = date(2026, 10, 3)


def estimate_for(**overrides):
    base = dict(
        target_amount=Decimal("250000"),
        current_amount=Decimal("50000"),
        minimum_income=Decimal("50000"),
        income_regularity=IncomeFrequency.MONTHLY,
        target_date=date(2026, 12, 3),
        today=TODAY,
    )
    base.update(overrides)
    return estimate(**base)


def goal_fields(**overrides):
    fields = dict(
        purpose="Trip", target_amount=Decimal("250000"), current_amount=Decimal("50000"),
        target_date=date(2030, 1, 1), currency=CurrencyType.UGX,
        income_regularity=IncomeFrequency.MONTHLY, minimum_income=Decimal("50000"),
    )
    fields.update(overrides)
    return fields


class TestEstimate:
    def test_the_worked_example(self):
        result = estimate_for()
        assert result.remaining == Decimal("200000")
        assert result.periods_until_target == 2
        assert result.required == Decimal("100000")
        assert result.suggested_pace == Decimal("25000")
        assert result.periods_at_suggested_pace == 8
        assert result.suggested_pace_date == date(2027, 6, 4)
        assert result.show_suggested_pace is True
        assert result.frequency == SavingFrequency.MONTHLY

    def test_required_is_rounded_up_to_the_next_thousand(self):
        result = estimate_for(target_amount=Decimal("100001"), current_amount=Decimal("0"),
                              minimum_income=Decimal("1000000"))
        assert result.required == Decimal("51000")

    def test_a_pace_within_half_of_income_is_not_offered_as_an_alternative(self):
        result = estimate_for(minimum_income=Decimal("400000"), target_amount=Decimal("150000"))
        assert result.required == Decimal("50000")
        assert result.show_suggested_pace is False

    def test_a_target_inside_one_period_needs_the_whole_remainder(self):
        result = estimate_for(target_date=date(2026, 10, 10))
        assert result.periods_until_target == 1
        assert result.required == Decimal("200000")

    def test_weekly_income_counts_weekly_periods(self):
        result = estimate_for(income_regularity=IncomeFrequency.WEEKLY, target_date=date(2026, 10, 31),
                              target_amount=Decimal("100000"), current_amount=Decimal("0"),
                              minimum_income=Decimal("20000"))
        assert result.periods_until_target == 4
        assert result.required == Decimal("25000")
        assert result.frequency == SavingFrequency.WEEKLY

    def test_varying_income_is_treated_monthly(self):
        assert estimate_for(income_regularity=IncomeFrequency.VARIES).frequency == SavingFrequency.MONTHLY


class TestGoalRequestRules:
    def test_a_goal_with_something_left_to_save_is_accepted(self):
        UserGoalCreate(**goal_fields())

    def test_a_goal_already_reached_is_rejected(self):
        with pytest.raises(ValidationError):
            UserGoalCreate(**goal_fields(current_amount=Decimal("250000")))

    def test_a_target_date_in_the_past_is_rejected(self):
        with pytest.raises(ValidationError):
            UserGoalCreate(**goal_fields(target_date=date(2020, 1, 1)))


def fixed_today():
    return patch("app.ai_agents.orchestrator.orchestrator.date", **{"today.return_value": TODAY})


def stub_agent(reply: str):
    def fake_init(self, instructions, prompts, agent_builder):
        self.captured_prompt = prompts

        async def run(**kwargs):
            return reply

        self.run = run
    return patch.object(Agents, "__init__", fake_init)


class TestOrchestratorOutput:
    @pytest.mark.asyncio
    async def test_the_amounts_come_from_the_calculation_not_the_model(self):
        goal = UserGoalCreate.model_construct(**goal_fields(target_date=date(2026, 12, 3)))
        reply = ('{"reason": "Still to save 200,000 divided by 2 pay periods gives 100,000.",'
                 ' "alternative_reason": "At 25,000 a month it takes 8 months."}')

        with fixed_today(), stub_agent(reply):
            result = await Orchestrator().recommendation(user_id="u1", user_goal=goal)

        assert result.saving_amount_recommended == Decimal("100000")
        assert result.frequency == SavingFrequency.MONTHLY
        assert result.reason.startswith("Still to save")
        assert result.alternatives[0].saving_amount_recommended == Decimal("25000")

    @pytest.mark.asyncio
    async def test_the_agent_is_given_the_calculated_figures(self):
        goal = UserGoalCreate.model_construct(**goal_fields(target_date=date(2026, 12, 3)))
        reply = '{"reason": "Explanation.", "alternative_reason": null}'

        captured = {}

        def fake_init(self, instructions, prompts, agent_builder):
            captured["prompt"] = prompts

            async def run(**kwargs):
                return reply

            self.run = run

        with fixed_today(), patch.object(Agents, "__init__", fake_init):
            await Orchestrator().recommendation(user_id="u1", user_goal=goal)

        assert "Minimum to save per period (monthly) to reach the goal by the target date: 100000 UGX" in captured["prompt"]
        assert "Show the suggested-pace option: yes" in captured["prompt"]

    @pytest.mark.asyncio
    async def test_no_alternative_when_the_pace_is_already_comfortable(self):
        goal = UserGoalCreate.model_construct(**goal_fields(
            target_amount=Decimal("150000"), current_amount=Decimal("0"),
            minimum_income=Decimal("400000"), target_date=date(2026, 12, 3),
        ))

        with fixed_today(), stub_agent('{"reason": "Explanation.", "alternative_reason": null}'):
            result = await Orchestrator().recommendation(user_id="u1", user_goal=goal)

        assert result.alternatives is None
