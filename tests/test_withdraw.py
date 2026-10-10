from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.database.database import get_session
from app.model.mod_enum import CurrencyType, TransactionStatus
from app.routers.transaction.withdraw.withdraw_router import router
from app.schema.withdraw import UserWithdraw
from app.services.auth.current_user import get_current_user
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    AuthError,
    ExchangeRateUnavailableError,
    InsufficientBalanceError,
    InvalidOtpError,
    auth_error_handler,
)
from app.services.transaction.withdraw.withdraw import Withdraw

BASE = "/api/v1/withdraw"
PATCH_TARGET = "app.services.transaction.withdraw.withdraw"


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


async def fake_refresh(obj: Any) -> None:
    now = datetime.now(timezone.utc)
    if getattr(obj, "id", None) is None:
        obj.id = "generated-id"
    if getattr(obj, "created_at", None) is None:
        obj.created_at = now


def make_session(execute_results: list | None = None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    if execute_results is not None:
        session.execute = AsyncMock(side_effect=list(execute_results))
    session.refresh = AsyncMock(side_effect=fake_refresh)
    return session


def make_user(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(
        id="user123", email_address="user@example.com", email_verified=True,
        phone_number="+10005550000", phone_number_verified=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_account(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(id="acct-1", user_id="user123", currency=CurrencyType.UGX, balance=Decimal("100000"))
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_goal(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(id="goal-1", user_id="user123", current_amount=Decimal("50000"))
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_otp_record(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(id="otp-1", attempts=0, verified_at=None)
    fields.update(overrides)
    return SimpleNamespace(**fields)


def withdraw_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = dict(
        account_id="acct-1", goal_id=None, amount=Decimal("1000"),
        currency=CurrencyType.UGX, bank_reference="bank-1",
    )
    fields.update(overrides)
    return fields


def statements(session: AsyncMock) -> list[str]:
    return [str(c.args[0].compile(compile_kwargs={"literal_binds": True})) for c in session.execute.await_args_list]


# ---------------------------------------------------------------------------
# Withdraw.send_otp_withdraw - notification channel coverage (email + phone)
# ---------------------------------------------------------------------------

class TestServiceSendOtpWithdraw:
    @pytest.mark.asyncio
    async def test_sends_by_email_when_email_is_verified(self, monkeypatch):
        session = make_session([scalar_result(make_user(email_verified=True, phone_number_verified=False))])
        email_mock = MagicMock()
        monkeypatch.setattr("app.services.transaction.transaction.send_email_task", email_mock)
        sms_mock = AsyncMock()
        monkeypatch.setattr("app.services.transaction.transaction.send_otp_sms", sms_mock)
        monkeypatch.setattr(f"{PATCH_TARGET}.generate_otp_code", lambda: 12345678)
        monkeypatch.setattr(f"{PATCH_TARGET}.save_otp_code", AsyncMock())

        result = await Withdraw(session).send_otp_withdraw(user_id="user123")

        assert result.email_address == "user@example.com"
        assert result.phone_number is None
        email_mock.delay.assert_called_once()
        sms_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_by_phone_when_only_phone_is_verified(self, monkeypatch):
        session = make_session([scalar_result(make_user(email_verified=False, phone_number_verified=True))])
        email_mock = MagicMock()
        monkeypatch.setattr("app.services.transaction.transaction.send_email_task", email_mock)
        sms_mock = AsyncMock()
        monkeypatch.setattr("app.services.transaction.transaction.send_otp_sms", sms_mock)
        monkeypatch.setattr(f"{PATCH_TARGET}.generate_otp_code", lambda: 87654321)
        monkeypatch.setattr(f"{PATCH_TARGET}.save_otp_code", AsyncMock())

        result = await Withdraw(session).send_otp_withdraw(user_id="user123")

        assert result.phone_number == "+10005550000"
        assert result.email_address is None
        sms_mock.assert_awaited_once_with("+10005550000", "87654321")
        email_mock.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_when_neither_channel_is_verified(self, monkeypatch):
        session = make_session([scalar_result(make_user(email_verified=False, phone_number_verified=False))])
        monkeypatch.setattr(f"{PATCH_TARGET}.save_otp_code", AsyncMock())

        with pytest.raises(HTTPException) as exc_info:
            await Withdraw(session).send_otp_withdraw(user_id="user123")

        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Withdraw.save_withdraw
# ---------------------------------------------------------------------------

class TestServiceSaveWithdraw:
    @pytest.mark.asyncio
    async def test_debits_the_account_balance_same_currency(self, monkeypatch):
        account = make_account(balance=Decimal("10000"))
        session = make_session([
            scalar_result(make_user()), scalar_result(account),
            scalar_result(make_otp_record()), scalar_result(None),
        ])
        monkeypatch.setattr(f"{PATCH_TARGET}.get_otp_code", AsyncMock(return_value="12345678"))
        monkeypatch.setattr(f"{PATCH_TARGET}.delete_otp_code", AsyncMock())
        monkeypatch.setattr(f"{PATCH_TARGET}.get_exchange_rate", AsyncMock(return_value=Decimal("1")))
        monkeypatch.setattr("app.services.transaction.transaction.send_email_task", MagicMock())

        result = await Withdraw(session).save_withdraw(
            user_withdraw=UserWithdraw(**withdraw_fields(amount=Decimal("1000"))),
            user_id="user123", otp_code="12345678",
        )

        assert account.balance == Decimal("9000")
        assert result.amount_withdrawn == "1000.00"
        assert result.requested_amount == Decimal("1000")
        assert result.requested_currency == CurrencyType.UGX

    @pytest.mark.asyncio
    async def test_debits_the_account_and_the_linked_goal(self, monkeypatch):
        account = make_account(balance=Decimal("10000"))
        goal = make_goal(current_amount=Decimal("5000"))
        session = make_session([
            scalar_result(make_user()), scalar_result(account), scalar_result(goal),
            scalar_result(make_otp_record()), scalar_result(None),
        ])
        monkeypatch.setattr(f"{PATCH_TARGET}.get_otp_code", AsyncMock(return_value="12345678"))
        monkeypatch.setattr(f"{PATCH_TARGET}.delete_otp_code", AsyncMock())
        monkeypatch.setattr(f"{PATCH_TARGET}.get_exchange_rate", AsyncMock(return_value=Decimal("1")))
        monkeypatch.setattr("app.services.transaction.transaction.send_email_task", MagicMock())

        await Withdraw(session).save_withdraw(
            user_withdraw=UserWithdraw(**withdraw_fields(goal_id="goal-1", amount=Decimal("2000"))),
            user_id="user123", otp_code="12345678",
        )

        assert account.balance == Decimal("8000")
        assert goal.current_amount == Decimal("3000")

    @pytest.mark.asyncio
    async def test_converts_currency_and_keeps_requested_and_settled_distinct(self, monkeypatch):
        account = make_account(currency=CurrencyType.UGX, balance=Decimal("1000000"))
        session = make_session([
            scalar_result(make_user()), scalar_result(account),
            scalar_result(make_otp_record()), scalar_result(None),
        ])
        monkeypatch.setattr(f"{PATCH_TARGET}.get_otp_code", AsyncMock(return_value="12345678"))
        monkeypatch.setattr(f"{PATCH_TARGET}.delete_otp_code", AsyncMock())
        monkeypatch.setattr(f"{PATCH_TARGET}.get_exchange_rate", AsyncMock(return_value=Decimal("3700")))
        monkeypatch.setattr("app.services.transaction.transaction.send_email_task", MagicMock())

        result = await Withdraw(session).save_withdraw(
            user_withdraw=UserWithdraw(**withdraw_fields(amount=Decimal("10"), currency=CurrencyType.USD)),
            user_id="user123", otp_code="12345678",
        )

        assert account.balance == Decimal("963000")   # 1,000,000 - 37,000
        assert result.amount_withdrawn == "37000.00"
        assert result.currency == CurrencyType.UGX
        assert result.requested_amount == Decimal("10")
        assert result.requested_currency == CurrencyType.USD

    @pytest.mark.asyncio
    async def test_notification_states_the_requested_currency_not_the_settled_one(self, monkeypatch):
        account = make_account(currency=CurrencyType.UGX, balance=Decimal("1000000"))
        session = make_session([
            scalar_result(make_user()), scalar_result(account),
            scalar_result(make_otp_record()), scalar_result(None),
        ])
        monkeypatch.setattr(f"{PATCH_TARGET}.get_otp_code", AsyncMock(return_value="12345678"))
        monkeypatch.setattr(f"{PATCH_TARGET}.delete_otp_code", AsyncMock())
        monkeypatch.setattr(f"{PATCH_TARGET}.get_exchange_rate", AsyncMock(return_value=Decimal("3700")))
        email_mock = MagicMock()
        monkeypatch.setattr("app.services.transaction.transaction.send_email_task", email_mock)

        await Withdraw(session).save_withdraw(
            user_withdraw=UserWithdraw(**withdraw_fields(amount=Decimal("10"), currency=CurrencyType.USD)),
            user_id="user123", otp_code="12345678",
        )

        message = email_mock.delay.call_args.args[2]
        assert "10 USD" in message
        assert "37000" not in message

    @pytest.mark.asyncio
    async def test_insufficient_balance_is_rejected_plainly(self, monkeypatch):
        account = make_account(balance=Decimal("500"))
        session = make_session([
            scalar_result(make_user()), scalar_result(account), scalar_result(make_otp_record()),
        ])
        monkeypatch.setattr(f"{PATCH_TARGET}.get_otp_code", AsyncMock(return_value="12345678"))
        monkeypatch.setattr(f"{PATCH_TARGET}.delete_otp_code", AsyncMock())
        monkeypatch.setattr(f"{PATCH_TARGET}.get_exchange_rate", AsyncMock(return_value=Decimal("1")))

        with pytest.raises(InsufficientBalanceError) as exc_info:
            await Withdraw(session).save_withdraw(
                user_withdraw=UserWithdraw(**withdraw_fields(amount=Decimal("999999"))),
                user_id="user123", otp_code="12345678",
            )

        assert exc_info.value.detail == "Insufficient balance for this withdrawal"
        assert account.balance == Decimal("500")   # untouched

    @pytest.mark.asyncio
    async def test_insufficient_balance_still_records_a_failed_transaction(self, monkeypatch):
        account = make_account(balance=Decimal("500"))
        session = make_session([
            scalar_result(make_user()), scalar_result(account), scalar_result(make_otp_record()),
        ])
        monkeypatch.setattr(f"{PATCH_TARGET}.get_otp_code", AsyncMock(return_value="12345678"))
        monkeypatch.setattr(f"{PATCH_TARGET}.delete_otp_code", AsyncMock())
        monkeypatch.setattr(f"{PATCH_TARGET}.get_exchange_rate", AsyncMock(return_value=Decimal("1")))

        with pytest.raises(InsufficientBalanceError):
            await Withdraw(session).save_withdraw(
                user_withdraw=UserWithdraw(**withdraw_fields(amount=Decimal("999999"))),
                user_id="user123", otp_code="12345678",
            )

        saved_rows = [call.args[0] for call in session.add.call_args_list]
        failed = [row for row in saved_rows if getattr(row, "status", None) == TransactionStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].amount == Decimal("999999")
        assert failed[0].fx_rate_to_base == Decimal("1")

    @pytest.mark.asyncio
    async def test_exchange_rate_failure_records_a_failed_transaction_and_leaves_balance_untouched(self, monkeypatch):
        account = make_account(balance=Decimal("10000"))
        session = make_session([
            scalar_result(make_user()), scalar_result(account), scalar_result(make_otp_record()),
        ])
        monkeypatch.setattr(f"{PATCH_TARGET}.get_otp_code", AsyncMock(return_value="12345678"))
        monkeypatch.setattr(f"{PATCH_TARGET}.delete_otp_code", AsyncMock())
        monkeypatch.setattr(f"{PATCH_TARGET}.get_exchange_rate", AsyncMock(side_effect=ExchangeRateUnavailableError()))

        with pytest.raises(ExchangeRateUnavailableError):
            await Withdraw(session).save_withdraw(
                user_withdraw=UserWithdraw(**withdraw_fields(amount=Decimal("10"), currency=CurrencyType.USD)),
                user_id="user123", otp_code="12345678",
            )

        assert account.balance == Decimal("10000")   # untouched
        saved_rows = [call.args[0] for call in session.add.call_args_list]
        failed = [row for row in saved_rows if getattr(row, "status", None) == TransactionStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].fx_rate_to_base is None

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_user(self):
        session = make_session([scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await Withdraw(session).save_withdraw(
                user_withdraw=UserWithdraw(**withdraw_fields()), user_id="ghost", otp_code="12345678",
            )

    @pytest.mark.asyncio
    async def test_the_account_query_is_scoped_to_the_caller(self):
        session = make_session([scalar_result(make_user()), scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await Withdraw(session).save_withdraw(
                user_withdraw=UserWithdraw(**withdraw_fields(account_id="someone-elses-account")),
                user_id="user123", otp_code="12345678",
            )

        _, account_sql = statements(session)
        assert "user_id = 'user123'" in account_sql and "id = 'someone-elses-account'" in account_sql

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_goal(self):
        session = make_session([scalar_result(make_user()), scalar_result(make_account()), scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await Withdraw(session).save_withdraw(
                user_withdraw=UserWithdraw(**withdraw_fields(goal_id="ghost-goal")),
                user_id="user123", otp_code="12345678",
            )

    @pytest.mark.asyncio
    async def test_wrong_otp_is_rejected_and_the_attempt_is_recorded(self, monkeypatch):
        account = make_account(balance=Decimal("10000"))
        otp_record = make_otp_record()
        session = make_session([
            scalar_result(make_user()), scalar_result(account), scalar_result(otp_record),
        ])
        monkeypatch.setattr(f"{PATCH_TARGET}.get_otp_code", AsyncMock(return_value="12345678"))

        with pytest.raises(InvalidOtpError):
            await Withdraw(session).save_withdraw(
                user_withdraw=UserWithdraw(**withdraw_fields()), user_id="user123", otp_code="00000000",
            )

        assert otp_record.attempts == 1
        assert otp_record.verified_at is None
        assert account.balance == Decimal("10000")   # untouched


# ---------------------------------------------------------------------------
# Withdraw.get_account_withdrawals / get_all_withdrawals
# ---------------------------------------------------------------------------

class TestServiceWithdrawHistory:
    @pytest.mark.asyncio
    async def test_account_history_is_scoped_to_the_caller(self):
        session = make_session([scalar_result(make_account()), list_result([]), count_result(0)])

        await Withdraw(session).get_account_withdrawals(user_id="user123", account_id="acct-1")

        account_sql, _, _ = statements(session)
        assert "user_id = 'user123'" in account_sql

    @pytest.mark.asyncio
    async def test_an_intruder_gets_a_plain_404(self):
        session = make_session([scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await Withdraw(session).get_account_withdrawals(user_id="user123", account_id="someone-elses-account")

    @pytest.mark.asyncio
    async def test_all_withdrawals_are_scoped_to_the_caller_across_accounts(self):
        session = make_session([list_result([]), count_result(0)])

        await Withdraw(session).get_all_withdrawals(user_id="user123")

        listing_sql, _ = statements(session)
        assert "user_id = 'user123'" in listing_sql


# ---------------------------------------------------------------------------
# endpoints
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
        assert api.client.post(f"{BASE}/otp").status_code in (401, 403)

    @pytest.mark.parametrize("role", ["Agent", "Admin", "FundManager", "Broker"])
    def test_other_roles_cannot_use_withdraw_endpoints(self, api, role):
        api.app.dependency_overrides[get_current_user] = lambda: {"user_id": "user123", "role": role}
        assert api.client.post(f"{BASE}/otp").status_code == 403

    def test_all_is_not_shadowed_by_the_account_id_route(self, api, monkeypatch):
        monkeypatch.setattr(Withdraw, "get_all_withdrawals", AsyncMock(return_value=([], 0)))
        response = api.client.get(f"{BASE}/all")
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_confirm_requires_a_valid_body(self, api):
        response = api.client.post(BASE, json={"account_id": "a1"})
        assert response.status_code == 422

    def test_confirm_calls_the_service_and_returns_its_result(self, api, monkeypatch):
        fake_response = SimpleNamespace(
            first_name="A", last_name="B", account_id="acct-1", amount_withdrawn="1000.00",
            currency=CurrencyType.UGX, requested_amount=Decimal("1000"), requested_currency=CurrencyType.UGX,
            bank_reference="b1", withdraw_at=datetime.now(timezone.utc),
        )
        mock_save = AsyncMock(return_value=fake_response)
        monkeypatch.setattr(Withdraw, "save_withdraw", mock_save)

        response = api.client.post(BASE, json={**{**withdraw_fields(), "amount": "1000"}, "otp_code": "12345678"})

        assert response.status_code == 201
        assert mock_save.await_args.kwargs["otp_code"] == "12345678"
        assert mock_save.await_args.kwargs["user_id"] == "user123"

    def test_insufficient_balance_surfaces_as_400(self, api, monkeypatch):
        monkeypatch.setattr(Withdraw, "save_withdraw", AsyncMock(side_effect=InsufficientBalanceError()))

        response = api.client.post(BASE, json={**{**withdraw_fields(), "amount": "1000"}, "otp_code": "12345678"})

        assert response.status_code == 400
        assert response.json()["detail"] == "Insufficient balance for this withdrawal"
