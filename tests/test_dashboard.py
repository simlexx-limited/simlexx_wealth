from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database.database import get_session
from app.model.mod_enum import CurrencyType, SavingFrequency, TransactionStatus, TransactionType
from app.routers.dashboard.dashboard_router import router
from app.schema.dashboard import BalanceSummary
from app.schema.user_goal import GoalDashboardResponse
from app.services.auth.current_user import get_current_user
from app.services.auth.firebase_auth.auth_errors import AccountNotFoundError, AuthError, auth_error_handler
from app.services.dashboard.dashboard_service import DashboardService
from app.services.goal_management.goal_management import UserGoalManagement

BASE = "/api/v1/dashboard"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def scalar_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def list_result(values: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def count_result(value: int) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def make_session(execute_results: list | None = None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    if execute_results is not None:
        session.execute = AsyncMock(side_effect=list(execute_results))
    return session


def statements(session: AsyncMock) -> list[str]:
    return [str(c.args[0].compile(compile_kwargs={"literal_binds": True})) for c in session.execute.await_args_list]


def make_account(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(
        id="acct-1", user_id="user123", bank_name="KCB", account_number="0011",
        currency=CurrencyType.UGX, balance=Decimal("500000"), created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_transaction(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(
        id="tx-1", account_id="acct-1", goal_id=None, type=TransactionType.DEPOSIT, amount=Decimal("1000"),
        currency=CurrencyType.UGX, status=TransactionStatus.COMPLETED, bank_reference="b1",
        failure_reason=None, created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_goal_dashboard(**overrides: Any) -> GoalDashboardResponse:
    fields: dict[str, Any] = dict(
        id="goal-1", purpose="Trip to Nairobi", currency=CurrencyType.UGX,
        target_amount=Decimal("250000"), current_amount=Decimal("50000"),
        amount_remaining=Decimal("200000"), percent_complete=Decimal("20.00"),
        target_date=date(2030, 1, 1), total_contributed=Decimal("50000"),
        number_of_contributions=2, growth=Decimal("0"),
        next_recommended_amount=None, next_recommended_frequency=None,
    )
    fields.update(overrides)
    return GoalDashboardResponse(**fields)


# ---------------------------------------------------------------------------
# DashboardService.get_balance_summary
# ---------------------------------------------------------------------------

class TestServiceBalanceSummary:
    @pytest.mark.asyncio
    async def test_sums_balance_per_currency(self):
        accounts = [
            make_account(id="acct-1", currency=CurrencyType.UGX, balance=Decimal("500000")),
            make_account(id="acct-2", currency=CurrencyType.UGX, balance=Decimal("250000")),
            make_account(id="acct-3", currency=CurrencyType.USD, balance=Decimal("100")),
        ]
        session = make_session([list_result(accounts)])

        result = await DashboardService(session).get_balance_summary(user_id="user123")

        assert result.total_by_currency[CurrencyType.UGX] == Decimal("750000")
        assert result.total_by_currency[CurrencyType.USD] == Decimal("100")
        assert len(result.accounts) == 3

    @pytest.mark.asyncio
    async def test_no_accounts_means_an_empty_summary(self):
        session = make_session([list_result([])])

        result = await DashboardService(session).get_balance_summary(user_id="user123")

        assert result.accounts == []
        assert result.total_by_currency == {}

    @pytest.mark.asyncio
    async def test_is_scoped_to_the_caller(self):
        session = make_session([list_result([])])

        await DashboardService(session).get_balance_summary(user_id="user123")

        [accounts_sql] = statements(session)
        assert "user_id = 'user123'" in accounts_sql


# ---------------------------------------------------------------------------
# DashboardService.get_transactions
# ---------------------------------------------------------------------------

class TestServiceTransactions:
    @pytest.mark.asyncio
    async def test_lists_deposits_and_withdrawals_for_the_caller_newest_first(self):
        session = make_session([list_result([]), count_result(0)])

        await DashboardService(session).get_transactions(user_id="user123")

        listing_sql, _ = statements(session)
        assert "user_id = 'user123'" in listing_sql
        assert "ORDER BY" in listing_sql and "created_at DESC" in listing_sql

    @pytest.mark.asyncio
    async def test_filters_by_type(self):
        session = make_session([list_result([]), count_result(0)])

        await DashboardService(session).get_transactions(user_id="user123", type=TransactionType.WITHDRAWAL)

        listing_sql, _ = statements(session)
        assert "WITHDRAWAL" in listing_sql

    @pytest.mark.asyncio
    async def test_filters_by_status(self):
        session = make_session([list_result([]), count_result(0)])

        await DashboardService(session).get_transactions(user_id="user123", status=TransactionStatus.FAILED)

        listing_sql, _ = statements(session)
        assert "FAILED" in listing_sql

    @pytest.mark.asyncio
    async def test_filters_by_account_and_goal(self):
        session = make_session([list_result([]), count_result(0)])

        await DashboardService(session).get_transactions(user_id="user123", account_id="acct-1", goal_id="goal-1")

        listing_sql, _ = statements(session)
        assert "account_id = 'acct-1'" in listing_sql
        assert "goal_id = 'goal-1'" in listing_sql

    @pytest.mark.asyncio
    async def test_filters_by_date_range(self):
        session = make_session([list_result([]), count_result(0)])

        await DashboardService(session).get_transactions(
            user_id="user123", date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
        )

        listing_sql, _ = statements(session)
        assert "2026-01-01" in listing_sql
        assert "2026-01-31" in listing_sql

    @pytest.mark.asyncio
    async def test_pagination_offsets_by_page(self):
        session = make_session([list_result([]), count_result(0)])

        await DashboardService(session).get_transactions(user_id="user123", page=3, limit=10)

        listing_sql, _ = statements(session)
        assert "LIMIT 10" in listing_sql
        assert "OFFSET 20" in listing_sql

    @pytest.mark.asyncio
    async def test_returns_items_and_total(self):
        items = [make_transaction(id="tx-1"), make_transaction(id="tx-2")]
        session = make_session([list_result(items), count_result(2)])

        results, total = await DashboardService(session).get_transactions(user_id="user123")

        assert [t.id for t in results] == ["tx-1", "tx-2"]
        assert total == 2


# ---------------------------------------------------------------------------
# DashboardService.get_overview
# ---------------------------------------------------------------------------

class TestServiceOverview:
    @pytest.mark.asyncio
    async def test_combines_balance_growth_goal_count_and_recent_transactions(self, monkeypatch):
        session = make_session()
        balance = BalanceSummary(accounts=[], total_by_currency={CurrencyType.UGX: Decimal("500000")})
        monkeypatch.setattr(DashboardService, "get_balance_summary", AsyncMock(return_value=balance))

        goal_dashboards = [
            make_goal_dashboard(id="goal-1", currency=CurrencyType.UGX, growth=Decimal("1000")),
            make_goal_dashboard(id="goal-2", currency=CurrencyType.UGX, growth=Decimal("500")),
        ]
        monkeypatch.setattr(UserGoalManagement, "list_goal_dashboards", AsyncMock(return_value=goal_dashboards))

        recent_tx = make_transaction(id="tx-1")
        monkeypatch.setattr(DashboardService, "get_transactions", AsyncMock(return_value=([recent_tx], 1)))

        result = await DashboardService(session).get_overview(user_id="user123")

        assert result.total_balance_by_currency[CurrencyType.UGX] == Decimal("500000")
        assert result.total_growth_by_currency[CurrencyType.UGX] == Decimal("1500")
        assert result.active_goal_count == 2
        assert len(result.recent_transactions) == 1
        assert result.recent_transactions[0].id == "tx-1"

    @pytest.mark.asyncio
    async def test_no_goals_means_zero_growth_and_zero_active_goals(self, monkeypatch):
        session = make_session()
        monkeypatch.setattr(
            DashboardService, "get_balance_summary",
            AsyncMock(return_value=BalanceSummary(accounts=[], total_by_currency={})),
        )
        monkeypatch.setattr(UserGoalManagement, "list_goal_dashboards", AsyncMock(return_value=[]))
        monkeypatch.setattr(DashboardService, "get_transactions", AsyncMock(return_value=([], 0)))

        result = await DashboardService(session).get_overview(user_id="user123")

        assert result.total_growth_by_currency == {}
        assert result.active_goal_count == 0
        assert result.recent_transactions == []


# ---------------------------------------------------------------------------
# endpoints - real JSON requests
# ---------------------------------------------------------------------------

@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "user123", "role": "User"}
    session = make_session()
    app.dependency_overrides[get_session] = lambda: session
    return SimpleNamespace(client=TestClient(app), app=app, session=session)


class TestEndpointRouting:
    def test_requires_a_token(self, api):
        del api.app.dependency_overrides[get_current_user]
        assert api.client.get(f"{BASE}/overview").status_code in (401, 403)

    @pytest.mark.parametrize("role", ["Agent", "Admin", "FundManager", "Broker"])
    def test_other_roles_cannot_use_dashboard_endpoints(self, api, role):
        api.app.dependency_overrides[get_current_user] = lambda: {"user_id": "user123", "role": role}
        assert api.client.get(f"{BASE}/overview").status_code == 403


class TestOverviewEndpoint:
    def test_returns_the_overview_from_the_service(self, api, monkeypatch):
        fake_overview = SimpleNamespace(
            total_balance_by_currency={"UGX": Decimal("500000")},
            total_growth_by_currency={"UGX": Decimal("1500")},
            active_goal_count=2,
            recent_transactions=[],
        )
        mock_overview = AsyncMock(return_value=fake_overview)
        monkeypatch.setattr(DashboardService, "get_overview", mock_overview)

        response = api.client.get(f"{BASE}/overview")

        assert response.status_code == 200
        assert response.json()["active_goal_count"] == 2
        assert mock_overview.await_args.kwargs["user_id"] == "user123"


class TestBalanceEndpoint:
    def test_returns_the_balance_summary_from_the_service(self, api, monkeypatch):
        fake_summary = BalanceSummary(accounts=[], total_by_currency={CurrencyType.UGX: Decimal("750000")})
        mock_balance = AsyncMock(return_value=fake_summary)
        monkeypatch.setattr(DashboardService, "get_balance_summary", mock_balance)

        response = api.client.get(f"{BASE}/balance")

        assert response.status_code == 200
        assert response.json()["total_by_currency"]["UGX"] == "750000"
        assert mock_balance.await_args.kwargs["user_id"] == "user123"


class TestTransactionsEndpoint:
    def test_returns_a_page_of_transactions(self, api, monkeypatch):
        items = [make_transaction(id="tx-1"), make_transaction(id="tx-2")]
        mock_transactions = AsyncMock(return_value=(items, 2))
        monkeypatch.setattr(DashboardService, "get_transactions", mock_transactions)

        response = api.client.get(f"{BASE}/transactions")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert [item["id"] for item in body["items"]] == ["tx-1", "tx-2"]

    def test_passes_filters_through_to_the_service(self, api, monkeypatch):
        mock_transactions = AsyncMock(return_value=([], 0))
        monkeypatch.setattr(DashboardService, "get_transactions", mock_transactions)

        response = api.client.get(
            f"{BASE}/transactions",
            params={
                "type": "withdrawal", "status": "completed", "account_id": "acct-1",
                "goal_id": "goal-1", "date_from": "2026-01-01", "date_to": "2026-01-31",
                "page": 2, "limit": 5,
            },
        )

        assert response.status_code == 200
        kwargs = mock_transactions.await_args.kwargs
        assert kwargs["user_id"] == "user123"
        assert kwargs["type"] == TransactionType.WITHDRAWAL
        assert kwargs["status"] == TransactionStatus.COMPLETED
        assert kwargs["account_id"] == "acct-1"
        assert kwargs["goal_id"] == "goal-1"
        assert kwargs["date_from"] == date(2026, 1, 1)
        assert kwargs["date_to"] == date(2026, 1, 31)
        assert kwargs["page"] == 2
        assert kwargs["limit"] == 5

    def test_an_invalid_type_is_422_not_500(self, api):
        response = api.client.get(f"{BASE}/transactions", params={"type": "not-a-type"})
        assert response.status_code == 422


class TestGoalsEndpoint:
    def test_returns_progress_for_every_active_goal(self, api, monkeypatch):
        dashboards = [make_goal_dashboard(id="goal-1"), make_goal_dashboard(id="goal-2")]
        mock_goals = AsyncMock(return_value=dashboards)
        monkeypatch.setattr(UserGoalManagement, "list_goal_dashboards", mock_goals)

        response = api.client.get(f"{BASE}/goals")

        assert response.status_code == 200
        body = response.json()
        assert [item["id"] for item in body] == ["goal-1", "goal-2"]
        assert mock_goals.await_args.kwargs["user_id"] == "user123"


class TestGoalDashboardEndpoint:
    def test_returns_the_dashboard_for_the_callers_own_goal(self, api, monkeypatch):
        fake_dashboard = make_goal_dashboard(
            id="goal-1", growth=Decimal("1234.56"),
            next_recommended_amount=Decimal("50000"), next_recommended_frequency=SavingFrequency.MONTHLY,
        )
        mock_dashboard = AsyncMock(return_value=fake_dashboard)
        monkeypatch.setattr(UserGoalManagement, "get_goal_dashboard", mock_dashboard)

        response = api.client.get(f"{BASE}/goal/goal-1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "goal-1"
        assert body["growth"] == "1234.56"
        assert mock_dashboard.await_args.kwargs["goal_id"] == "goal-1"
        assert mock_dashboard.await_args.kwargs["user_id"] == "user123"

    def test_an_unknown_goal_is_404(self, api, monkeypatch):
        monkeypatch.setattr(
            UserGoalManagement, "get_goal_dashboard",
            AsyncMock(side_effect=AccountNotFoundError(detail="No goal found for this user.")),
        )
        response = api.client.get(f"{BASE}/goal/ghost-goal")
        assert response.status_code == 404
