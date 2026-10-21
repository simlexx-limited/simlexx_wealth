from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config.setting import get_settings
from app.database.database import get_session
from app.model.mod_enum import CurrencyType, GoalStatus, IncomeFrequency, SavingFrequency
from app.model.models import GoalRecommendations, Notifications, SystemLogs
from app.routers.onboarding.user_goal import router
from app.schema.user_goal import AlternativeSavingStrategy, SavingRecommendation, UserGoalCreate
from app.services.auth.current_user import get_current_user
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    AuthError,
    ProductNotFoundError,
    auth_error_handler,
)
from app.services.goal_management.goal_management import UserGoalManagement

BASE = "/api/v1/user_goal"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def goal_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = dict(
        purpose="Trip to Nairobi",
        target_amount=Decimal("250000"),
        current_amount=Decimal("50000"),
        target_date=date(2030, 1, 1),
        currency=CurrencyType.UGX,
        income_regularity=IncomeFrequency.MONTHLY,
        minimum_income=Decimal("50000"),
    )
    fields.update(overrides)
    return fields


def scalar_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def list_result(values: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def statements(session: AsyncMock) -> list[str]:
    return [str(c.args[0].compile(compile_kwargs={"literal_binds": True})) for c in session.execute.await_args_list]


async def fake_refresh(obj: Any) -> None:
    """Simulates what a real commit+refresh against Postgres would populate."""
    now = datetime.now(timezone.utc)
    if getattr(obj, "id", None) is None:
        obj.id = "generated-id"
    if getattr(obj, "created_at", None) is None:
        obj.created_at = now
    if getattr(obj, "updated_at", None) is None:
        obj.updated_at = now


def make_session(execute_results: list | None = None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    if execute_results is not None:
        session.execute = AsyncMock(side_effect=list(execute_results))
    session.refresh = AsyncMock(side_effect=fake_refresh)
    return session


def make_recommendation(**overrides: Any) -> SavingRecommendation:
    fields: dict[str, Any] = dict(
        saving_amount_recommended=Decimal("100000"), currency=CurrencyType.UGX,
        frequency=SavingFrequency.MONTHLY,
        reason="Still to save 200000 divided by 2 pay periods gives 100000.",
        alternatives=None,
    )
    fields.update(overrides)
    return SavingRecommendation(**fields)


# ---------------------------------------------------------------------------
# UserGoalManagement.create_goal
# ---------------------------------------------------------------------------

class TestServiceCreateGoal:
    @pytest.mark.asyncio
    async def test_creates_the_goal_with_its_income_snapshot(self):
        session = make_session([scalar_result("user123")])

        goal = await UserGoalManagement(session).create_goal(
            user_goal=UserGoalCreate(**goal_fields()), user_id="user123",
        )

        assert goal.user_id == "user123"
        assert goal.purpose == "Trip to Nairobi"
        assert goal.income_regularity == IncomeFrequency.MONTHLY
        assert goal.minimum_income == Decimal("50000")
        assert goal.goal_status == GoalStatus.ONGOING
        assert goal.product_id is None

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_user(self):
        session = make_session([scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await UserGoalManagement(session).create_goal(
                user_goal=UserGoalCreate(**goal_fields()), user_id="ghost",
            )

        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_product(self):
        session = make_session([scalar_result("user123"), scalar_result(None)])

        with pytest.raises(ProductNotFoundError):
            await UserGoalManagement(session).create_goal(
                user_goal=UserGoalCreate(**goal_fields(product_id="ghost-product")), user_id="user123",
            )

        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_a_real_product(self):
        session = make_session([scalar_result("user123"), scalar_result("prod-1")])

        goal = await UserGoalManagement(session).create_goal(
            user_goal=UserGoalCreate(**goal_fields(product_id="prod-1")), user_id="user123",
        )

        assert goal.product_id == "prod-1"

    @pytest.mark.asyncio
    async def test_logs_and_notifies_on_success(self):
        session = make_session([scalar_result("user123")])

        await UserGoalManagement(session).create_goal(
            user_goal=UserGoalCreate(**goal_fields()), user_id="user123",
        )

        saved = [call.args[0] for call in session.add.call_args_list]
        assert sum(isinstance(row, SystemLogs) for row in saved) == 1
        assert sum(isinstance(row, Notifications) for row in saved) == 1
        assert session.commit.await_count == 3   # the goal, the log, the notification


# ---------------------------------------------------------------------------
# UserGoalManagement.get_goal_recommendation
# ---------------------------------------------------------------------------

class TestServiceGetGoalRecommendation:
    @pytest.mark.asyncio
    async def test_stores_the_request_alongside_the_response(self):
        fake_orchestrator = SimpleNamespace(recommendation=AsyncMock(return_value=make_recommendation()))
        session = make_session()

        with patch("app.services.goal_management.goal_management.orchestrator", return_value=fake_orchestrator):
            result = await UserGoalManagement(session).get_goal_recommendation(
                user_goal=UserGoalCreate(**goal_fields()), user_id="user123",
            )

        assert result.saving_amount_recommended == Decimal("100000")
        session.add.assert_called_once()
        saved = session.add.call_args.args[0]
        assert isinstance(saved, GoalRecommendations)
        assert saved.user_id == "user123"
        assert saved.request["purpose"] == "Trip to Nairobi"
        assert saved.request["minimum_income"] == "50000"
        assert saved.saving_amount_recommended == Decimal("100000")
        assert saved.alternatives is None
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_product_before_calling_the_agent(self):
        fake_orchestrator = SimpleNamespace(recommendation=AsyncMock())
        session = make_session([scalar_result(None)])

        with patch("app.services.goal_management.goal_management.orchestrator", return_value=fake_orchestrator):
            with pytest.raises(ProductNotFoundError):
                await UserGoalManagement(session).get_goal_recommendation(
                    user_goal=UserGoalCreate(**goal_fields(product_id="ghost-product")), user_id="user123",
                )

        fake_orchestrator.recommendation.assert_not_called()
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_chosen_product_is_passed_to_the_agent(self):
        product_row = SimpleNamespace(id="prod-1", product_name="Growth Fund")
        fake_orchestrator = SimpleNamespace(recommendation=AsyncMock(return_value=make_recommendation()))
        session = make_session([scalar_result(product_row)])

        with patch("app.services.goal_management.goal_management.orchestrator", return_value=fake_orchestrator):
            await UserGoalManagement(session).get_goal_recommendation(
                user_goal=UserGoalCreate(**goal_fields(product_id="prod-1")), user_id="user123",
            )

        assert fake_orchestrator.recommendation.call_args.kwargs["product"] is product_row

    @pytest.mark.asyncio
    async def test_alternatives_are_stored_as_plain_json_not_pydantic_objects(self):
        recommendation = make_recommendation(alternatives=[
            AlternativeSavingStrategy(saving_amount_recommended=Decimal("25000"),
                                      frequency=SavingFrequency.MONTHLY, reason="A slower pace.")
        ])
        fake_orchestrator = SimpleNamespace(recommendation=AsyncMock(return_value=recommendation))
        session = make_session()

        with patch("app.services.goal_management.goal_management.orchestrator", return_value=fake_orchestrator):
            await UserGoalManagement(session).get_goal_recommendation(
                user_goal=UserGoalCreate(**goal_fields()), user_id="user123",
            )

        saved = session.add.call_args.args[0]
        assert saved.alternatives == [
            {"saving_amount_recommended": "25000", "frequency": "monthly", "reason": "A slower pace."}
        ]


# ---------------------------------------------------------------------------
# UserGoalManagement.get_goal_dashboard
# ---------------------------------------------------------------------------

def make_goal_row(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(
        id="goal-1", user_id="user123", purpose="Trip to Nairobi", currency=CurrencyType.UGX,
        target_amount=Decimal("250000"), current_amount=Decimal("50000"),
        target_date=date(2030, 1, 1), minimum_income=Decimal("50000"),
        income_regularity=IncomeFrequency.MONTHLY, product_id=None,
        created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def sum_count_result(total: Decimal, count: int) -> MagicMock:
    result = MagicMock()
    result.one.return_value = (total, count)
    return result


def make_product_row(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(id="prod-1", annual_return_rate=Decimal("0.11"))
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestServiceGoalDashboard:
    @pytest.mark.asyncio
    async def test_progress_fields_without_a_product(self):
        goal = make_goal_row(target_amount=Decimal("250000"), current_amount=Decimal("50000"))
        session = make_session([scalar_result(goal), sum_count_result(Decimal("50000"), 2)])

        result = await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="goal-1")

        assert result.target_amount == Decimal("250000")
        assert result.current_amount == Decimal("50000")
        assert result.amount_remaining == Decimal("200000")
        assert result.percent_complete == Decimal("20.00")
        assert result.total_contributed == Decimal("50000")
        assert result.number_of_contributions == 2
        assert result.growth == Decimal("0")

    @pytest.mark.asyncio
    async def test_growth_is_zero_without_a_product(self):
        goal = make_goal_row(product_id=None)
        session = make_session([scalar_result(goal), sum_count_result(Decimal("0"), 0)])

        result = await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="goal-1")

        assert result.growth == Decimal("0")
        session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_growth_is_simulated_from_the_products_return_rate(self):
        days_held = 73
        created_at = datetime.now(timezone.utc) - timedelta(days=days_held)
        goal = make_goal_row(product_id="prod-1", current_amount=Decimal("100000"), created_at=created_at)
        session = make_session([scalar_result(goal), sum_count_result(Decimal("100000"), 1)])
        session.get = AsyncMock(return_value=make_product_row(annual_return_rate=Decimal("0.11")))

        result = await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="goal-1")

        expected = (Decimal("100000") * Decimal("0.11") * Decimal(days_held) / Decimal("365.0")).quantize(Decimal("0.01"))
        assert result.growth == expected
        session.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_growth_is_zero_when_the_product_has_no_return_rate_set(self):
        goal = make_goal_row(product_id="prod-1")
        session = make_session([scalar_result(goal), sum_count_result(Decimal("0"), 0)])
        session.get = AsyncMock(return_value=make_product_row(annual_return_rate=None))

        result = await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="goal-1")

        assert result.growth == Decimal("0")

    @pytest.mark.asyncio
    async def test_the_growth_window_can_be_tuned_via_settings(self, monkeypatch):
        days_held = 30
        created_at = datetime.now(timezone.utc) - timedelta(days=days_held)
        goal = make_goal_row(product_id="prod-1", current_amount=Decimal("100000"), created_at=created_at)
        session = make_session([scalar_result(goal), sum_count_result(Decimal("0"), 0)])
        session.get = AsyncMock(return_value=make_product_row(annual_return_rate=Decimal("0.11")))
        monkeypatch.setattr(get_settings(), "GROWTH_DAYS_PER_YEAR", 30.0)

        result = await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="goal-1")

        expected = (Decimal("100000") * Decimal("0.11") * Decimal(days_held) / Decimal("30.0")).quantize(Decimal("0.01"))
        assert result.growth == expected

    @pytest.mark.asyncio
    async def test_recommendation_is_recomputed_live_not_fetched_from_storage(self):
        goal = make_goal_row(target_amount=Decimal("250000"), current_amount=Decimal("50000"),
                             target_date=date(2030, 1, 1), minimum_income=Decimal("50000"),
                             income_regularity=IncomeFrequency.MONTHLY)
        session = make_session([scalar_result(goal), sum_count_result(Decimal("0"), 0)])

        result = await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="goal-1")

        assert result.next_recommended_amount is not None
        assert result.next_recommended_frequency == SavingFrequency.MONTHLY
        assert session.execute.await_count == 2   # goal lookup + deposit stats only - nothing fetched for a recommendation

    @pytest.mark.asyncio
    async def test_no_recommendation_when_the_goal_is_already_reached(self):
        goal = make_goal_row(target_amount=Decimal("100000"), current_amount=Decimal("100000"))
        session = make_session([scalar_result(goal), sum_count_result(Decimal("100000"), 1)])

        result = await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="goal-1")

        assert result.next_recommended_amount is None
        assert result.next_recommended_frequency is None

    @pytest.mark.asyncio
    async def test_no_recommendation_when_the_target_date_has_passed(self):
        goal = make_goal_row(target_date=date(2020, 1, 1))
        session = make_session([scalar_result(goal), sum_count_result(Decimal("0"), 0)])

        result = await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="goal-1")

        assert result.next_recommended_amount is None

    @pytest.mark.asyncio
    async def test_an_unknown_goal_is_404(self):
        session = make_session([scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await UserGoalManagement(session).get_goal_dashboard(user_id="user123", goal_id="ghost-goal")


# ---------------------------------------------------------------------------
# UserGoalManagement.list_goal_dashboards
# ---------------------------------------------------------------------------

class TestServiceListGoalDashboards:
    @pytest.mark.asyncio
    async def test_returns_a_dashboard_per_active_goal(self):
        goals = [
            make_goal_row(id="goal-1", purpose="Trip to Nairobi"),
            make_goal_row(id="goal-2", purpose="Emergency fund"),
        ]
        session = make_session([
            list_result(goals),
            sum_count_result(Decimal("0"), 0), sum_count_result(Decimal("0"), 0),
        ])

        results = await UserGoalManagement(session).list_goal_dashboards(user_id="user123")

        assert [r.id for r in results] == ["goal-1", "goal-2"]
        assert [r.purpose for r in results] == ["Trip to Nairobi", "Emergency fund"]

    @pytest.mark.asyncio
    async def test_the_goal_listing_is_scoped_to_the_caller_and_active_statuses(self):
        session = make_session([list_result([])])

        await UserGoalManagement(session).list_goal_dashboards(user_id="user123")

        [goals_sql] = statements(session)
        assert "user_id = 'user123'" in goals_sql
        assert "goal_status IN" in goals_sql

    @pytest.mark.asyncio
    async def test_no_active_goals_means_an_empty_list(self):
        session = make_session([list_result([])])

        results = await UserGoalManagement(session).list_goal_dashboards(user_id="user123")

        assert results == []


# ---------------------------------------------------------------------------
# endpoints - real JSON requests
# ---------------------------------------------------------------------------

GOAL_BODY = {
    "purpose": "Trip to Nairobi",
    "target_amount": "250000",
    "current_amount": "50000",
    "target_date": "2030-01-01",
    "currency": "UGX",
    "income_regularity": "monthly",
    "minimum_income": "50000",
}


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)

    session = make_session([scalar_result("user123")])
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "user123", "role": "User"}
    app.dependency_overrides[get_session] = lambda: session

    return SimpleNamespace(client=TestClient(app), app=app, session=session)


class TestCreateEndpoint:
    def test_creates_the_goal_and_returns_it(self, api):
        response = api.client.post(f"{BASE}/goal/create", json=GOAL_BODY)

        assert response.status_code == 201
        body = response.json()
        assert body["user_id"] == "user123"
        assert body["minimum_income"] == "50000"
        assert body["goal_status"] == "ongoing"

    @pytest.mark.parametrize("field,value", [
        ("target_amount", "0"),
        ("current_amount", "250000"),
        ("target_date", "2020-01-01"),
        ("minimum_income", "0"),
    ])
    def test_invalid_values_are_422_not_500(self, api, field, value):
        response = api.client.post(f"{BASE}/goal/create", json={**GOAL_BODY, field: value})
        assert response.status_code == 422

    def test_missing_minimum_income_is_422(self, api):
        body = {k: v for k, v in GOAL_BODY.items() if k != "minimum_income"}
        response = api.client.post(f"{BASE}/goal/create", json=body)
        assert response.status_code == 422

    def test_requires_a_token(self, api):
        del api.app.dependency_overrides[get_current_user]
        response = api.client.post(f"{BASE}/goal/create", json=GOAL_BODY)
        assert response.status_code in (401, 403)

    @pytest.mark.parametrize("role", ["Agent", "Admin", "FundManager", "Broker"])
    def test_other_roles_cannot_create_a_goal(self, api, role):
        api.app.dependency_overrides[get_current_user] = lambda: {"user_id": "user123", "role": role}
        response = api.client.post(f"{BASE}/goal/create", json=GOAL_BODY)
        assert response.status_code == 403


class TestRecommendationEndpoint:
    def test_returns_the_calculated_recommendation(self, api, monkeypatch):
        fake_orchestrator = SimpleNamespace(recommendation=AsyncMock(return_value=make_recommendation()))
        monkeypatch.setattr(
            "app.services.goal_management.goal_management.orchestrator",
            lambda: fake_orchestrator,
        )
        api.session.execute = AsyncMock()

        response = api.client.post(f"{BASE}/goal/recommendation", json=GOAL_BODY)

        assert response.status_code == 200
        body = response.json()
        assert body["saving_amount_recommended"] == "100000"
        assert body["frequency"] == "monthly"

    def test_requires_a_token(self, api):
        del api.app.dependency_overrides[get_current_user]
        response = api.client.post(f"{BASE}/goal/recommendation", json=GOAL_BODY)
        assert response.status_code in (401, 403)


# The goal dashboard endpoint itself (GET /goal/dashboard/{goal_id}) moved to
# the dedicated dashboard router - see tests/test_dashboard.py.
