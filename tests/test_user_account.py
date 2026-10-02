from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import app.services.user_account.account_creation as service_module
from app.database.database import get_session
from app.model.mod_enum import CurrencyType
from app.routers.user_account import user_account_router as router_module
from app.routers.user_account.user_account_router import router
from app.schema.user_account_schema import AccountDBSchema, AccountUpdate
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    AuthError,
    DuplicateAccountError,
    NothingToUpdateError,
    auth_error_handler,
)
from app.services.auth.current_user import get_current_user
from app.services.user_account.account_creation import UserAccountService

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
TEXT = b"this is not an image or a pdf, just text"

DOC_KEY = "users/user123/account/user123_doc.pdf"
DUMMY_ENDPOINT = "https://test-account.r2.cloudflarestorage.com"   # see offline_signing in conftest.py
BASE = "/api/v1/account"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_account_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "bank_name": "Stanbic Bank",
        "account_number": "9100012345",
        "currency": CurrencyType.UGX,
        "account_document_id": "REF-0001",
        "account_document": DOC_KEY,
    }
    payload.update(overrides)
    return payload


def make_account_row(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(
        id="acct-1", user_id="user123", bank_name="Stanbic Bank", account_number="9100012345",
        balance=Decimal("0"), currency=CurrencyType.UGX, account_document_id="REF-0001",
        account_document=DOC_KEY, created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def scalar_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def list_result(values: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


async def fake_refresh(obj: Any) -> None:
    """Simulates what a real commit+refresh against Postgres would populate:
    defaults that only exist after the row round-trips through the database."""
    if getattr(obj, "id", None) is None:
        obj.id = "generated-id"
    if getattr(obj, "created_at", None) is None:
        obj.created_at = datetime.now(timezone.utc)


def make_session(execute_results: list | None = None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()   # real AsyncSession.add() is sync, not async
    # default: a single "the user exists" result, for callers that only need
    # check_user_exist to pass (e.g. create_account's happy path)
    session.execute = AsyncMock(
        side_effect=list(execute_results) if execute_results is not None else None,
        return_value=scalar_result("user123") if execute_results is None else None,
    )
    session.refresh = AsyncMock(side_effect=fake_refresh)
    return session


def statements(session: AsyncMock) -> list[str]:
    """Every statement actually sent to the database, as literal SQL - lets a
    test prove a query is scoped to the right user, not just that it returns
    whatever the mock was told to return."""
    return [str(c.args[0].compile(compile_kwargs={"literal_binds": True})) for c in session.execute.await_args_list]


# ---------------------------------------------------------------------------
# UserAccountService.create_account
# ---------------------------------------------------------------------------

class TestServiceCreateAccount:
    @pytest.mark.asyncio
    async def test_links_a_new_account_to_an_existing_user(self):
        session = make_session([scalar_result("user123")])

        account = await UserAccountService(session).create_account(
            account=AccountDBSchema(user_id="user123", **make_account_payload()),
            user_id="user123",
        )

        assert account.user_id == "user123"
        assert account.bank_name == "Stanbic Bank"
        assert account.account_document_link.startswith(DUMMY_ENDPOINT) and DOC_KEY in account.account_document_link
        assert session.add.call_count == 2   # the account row, then the audit log
        assert session.commit.await_count == 2

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_user(self):
        session = make_session([scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await UserAccountService(session).create_account(
                account=AccountDBSchema(user_id="ghost", **make_account_payload()), user_id="ghost",
            )

        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_saved_row_uses_the_callers_user_id_not_the_payloads(self):
        """Regression: AccountDBSchema.model_dump() already carries user_id, so
        passing it again to UserAccount(**dump, user_id=...) once raised
        'got multiple values for keyword argument user_id'."""
        session = make_session([scalar_result("user123")])

        account = await UserAccountService(session).create_account(
            account=AccountDBSchema(user_id="someone-else", **make_account_payload()), user_id="user123",
        )

        assert account.user_id == "user123"

    @pytest.mark.asyncio
    async def test_duplicate_bank_and_account_number_is_rejected(self):
        session = make_session([scalar_result("user123")])
        session.commit = AsyncMock(side_effect=[IntegrityError("INSERT", {}, Exception("dup")), None])

        with pytest.raises(DuplicateAccountError):
            await UserAccountService(session).create_account(
                account=AccountDBSchema(user_id="user123", **make_account_payload()), user_id="user123",
            )

        session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# UserAccountService.get_all_accounts
# ---------------------------------------------------------------------------

class TestServiceGetAllAccounts:
    @pytest.mark.asyncio
    async def test_returns_every_account_for_the_caller(self):
        session = make_session([scalar_result("user123"), list_result([make_account_row()])])

        accounts = await UserAccountService(session).get_all_accounts(user_id="user123")

        assert len(accounts) == 1 and accounts[0].user_id == "user123"

    @pytest.mark.asyncio
    async def test_the_query_is_scoped_to_the_caller(self):
        """Regression: get_all_accounts once selected with no join to Users at
        all (an implicit cross join), leaking every client's accounts to any caller."""
        session = make_session([scalar_result("user123"), list_result([])])

        await UserAccountService(session).get_all_accounts(user_id="user123")

        _, accounts_sql = statements(session)
        assert "user_id = 'user123'" in accounts_sql

    @pytest.mark.asyncio
    async def test_newest_accounts_come_first(self):
        session = make_session([scalar_result("user123"), list_result([])])

        await UserAccountService(session).get_all_accounts(user_id="user123")

        _, accounts_sql = statements(session)
        assert "ORDER BY" in accounts_sql and "created_at DESC" in accounts_sql

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_user(self):
        session = make_session([scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await UserAccountService(session).get_all_accounts(user_id="ghost")


# ---------------------------------------------------------------------------
# UserAccountService.get_account
# ---------------------------------------------------------------------------

class TestServiceGetAccount:
    @pytest.mark.asyncio
    async def test_returns_one_of_the_callers_own_accounts(self):
        session = make_session([scalar_result("user123"), scalar_result(make_account_row())])

        account = await UserAccountService(session).get_account(user_id="user123", account_id="acct-1")

        assert account.id == "acct-1"

    @pytest.mark.asyncio
    async def test_the_query_is_scoped_to_the_caller(self):
        """Regression: get_account once had no ownership filter at all, so any
        authenticated client could fetch any other user's account by guessing its id."""
        session = make_session([scalar_result("user123"), scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await UserAccountService(session).get_account(user_id="user123", account_id="someone-elses-account")

        _, account_sql = statements(session)
        assert "user_id = 'user123'" in account_sql and "id = 'someone-elses-account'" in account_sql

    @pytest.mark.asyncio
    async def test_a_nonexistent_account_is_not_found(self):
        session = make_session([scalar_result("user123"), scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await UserAccountService(session).get_account(user_id="user123", account_id="nope")


# ---------------------------------------------------------------------------
# UserAccountService.update_account
# ---------------------------------------------------------------------------

class TestServiceUpdateAccount:
    @pytest.mark.asyncio
    async def test_updates_only_the_fields_sent(self):
        session = make_session([scalar_result(make_account_row())])

        updated = await UserAccountService(session).update_account(
            user_id="user123", account_id="acct-1", data=AccountUpdate(account_document_id="NEW-REF"),
        )

        assert updated.account_document_id == "NEW-REF"
        assert updated.account_document == DOC_KEY   # untouched - no file was sent

    @pytest.mark.asyncio
    async def test_a_new_document_replaces_the_old_one_in_storage(self, monkeypatch):
        """Unlike KYC (kept, never overwritten), an account's proof document is
        replaced: the old object must be deleted once the swap is committed."""
        row = make_account_row(account_document="users/user123/account/old.pdf")
        session = make_session([scalar_result(row)])
        delete_mock = AsyncMock()
        monkeypatch.setattr(service_module, "delete_key", delete_mock)

        updated = await UserAccountService(session).update_account(
            user_id="user123", account_id="acct-1",
            data=AccountUpdate(account_document="users/user123/account/new.pdf"),
        )

        assert updated.account_document == "users/user123/account/new.pdf"
        delete_mock.assert_awaited_once_with("users/user123/account/old.pdf")

    @pytest.mark.asyncio
    async def test_a_storage_delete_failure_does_not_fail_the_update(self, monkeypatch):
        row = make_account_row(account_document="users/user123/account/old.pdf")
        session = make_session([scalar_result(row)])
        monkeypatch.setattr(service_module, "delete_key", AsyncMock(side_effect=Exception("r2 is down")))

        updated = await UserAccountService(session).update_account(
            user_id="user123", account_id="acct-1",
            data=AccountUpdate(account_document="users/user123/account/new.pdf"),
        )

        assert updated.account_document == "users/user123/account/new.pdf"   # the swap itself still succeeded

    @pytest.mark.asyncio
    async def test_the_query_is_scoped_to_the_caller(self):
        session = make_session([scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await UserAccountService(session).update_account(
                user_id="user123", account_id="someone-elses-account",
                data=AccountUpdate(account_document_id="NEW-REF"),
            )

        account_sql, = statements(session)
        assert "user_id = 'user123'" in account_sql and "id = 'someone-elses-account'" in account_sql

    @pytest.mark.asyncio
    async def test_nothing_sent_is_rejected(self):
        session = make_session([scalar_result(make_account_row())])

        with pytest.raises(NothingToUpdateError):
            await UserAccountService(session).update_account(
                user_id="user123", account_id="acct-1", data=AccountUpdate(),
            )

        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_nonexistent_account_is_not_found(self):
        session = make_session([scalar_result(None)])

        with pytest.raises(AccountNotFoundError):
            await UserAccountService(session).update_account(
                user_id="user123", account_id="nope", data=AccountUpdate(account_document_id="X"),
            )


# ---------------------------------------------------------------------------
# endpoints - real multipart requests
# ---------------------------------------------------------------------------

FORM_FIELDS = {
    "bank_name": "Stanbic Bank",
    "account_number": "9100012345",
    "currency": "UGX",
    "account_document_id": "REF-0001",
}


@pytest.fixture
def api(monkeypatch):
    """The router mounted on a minimal app, with auth, the DB session and the
    bucket faked. Returns the client, the session, and the upload mock."""
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)

    session = make_session()   # a bare AsyncMock: check_user_exist's lookup is truthy by default
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "user123", "role": "User"}
    app.dependency_overrides[get_session] = lambda: session

    upload_mock = AsyncMock(return_value=DOC_KEY)
    monkeypatch.setattr(router_module, "upload", upload_mock)

    return SimpleNamespace(client=TestClient(app), app=app, session=session, upload=upload_mock)


def post_create(api, form=None, account_document=("doc.pdf", PDF, "application/pdf")):
    files = {}
    if account_document is not None:
        files["account_document"] = account_document
    return api.client.post(f"{BASE}/create", data=FORM_FIELDS if form is None else form, files=files)


class TestCreateEndpoint:
    def test_creates_the_account_and_returns_it(self, api):
        response = post_create(api)

        assert response.status_code == 201
        body = response.json()
        assert body["user_id"] == "user123"
        assert body["bank_name"] == "Stanbic Bank"
        # the bucket is private: the response carries a temporary signed link, never the raw key
        assert "account_document" not in body
        assert body["account_document_link"].startswith(DUMMY_ENDPOINT) and DOC_KEY in body["account_document_link"]
        assert "X-Amz-Signature=" in body["account_document_link"]
        assert "links_expire_at" in body
        api.session.commit.assert_awaited()

    def test_user_id_comes_from_the_token_not_the_form(self, api):
        post_create(api, form={**FORM_FIELDS, "user_id": "someone-else"})

        created = api.session.add.call_args_list[0].args[0]
        assert created.user_id == "user123"

    def test_sending_json_instead_of_multipart_is_rejected(self, api):
        """The router takes File/Form, not a JSON body - this follows the same
        upload pattern as KYC, never a mix of the two."""
        response = api.client.post(f"{BASE}/create", json={**FORM_FIELDS, "account_document": DOC_KEY})
        assert response.status_code == 422
        api.upload.assert_not_called()

    def test_the_document_is_uploaded_to_its_own_folder(self, api):
        post_create(api)

        call = api.upload.call_args
        assert call.kwargs["folder_name"] == "account" and call.kwargs["username"] == "user123"

    def test_a_photo_of_the_document_is_accepted_too(self, api):
        assert post_create(api, account_document=("doc.jpg", JPEG, "image/jpeg")).status_code == 201

    def test_a_non_image_non_pdf_document_is_rejected(self, api):
        response = post_create(api, account_document=("doc.txt", TEXT, "text/plain"))

        assert response.status_code == 415
        api.upload.assert_not_called()

    def test_a_document_over_5mb_is_rejected(self, api):
        oversized = PDF + b"\x00" * (6 * 1024 * 1024)
        response = post_create(api, account_document=("doc.pdf", oversized, "application/pdf"))

        assert response.status_code == 413
        api.upload.assert_not_called()

    def test_missing_document_is_422(self, api):
        assert post_create(api, account_document=None).status_code == 422
        api.upload.assert_not_called()

    def test_duplicate_account_is_409(self, api):
        api.session.commit = AsyncMock(side_effect=[IntegrityError("INSERT", {}, Exception("dup")), None])

        assert post_create(api).status_code == 409


class TestListAndGetEndpoints:
    def test_lists_only_the_callers_accounts(self, api):
        api.session.execute = AsyncMock(side_effect=[scalar_result("user123"), list_result([make_account_row()])])

        response = api.client.get(f"{BASE}/all")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1 and body[0]["user_id"] == "user123"
        assert "account_document" not in body[0]

    def test_gets_one_of_the_callers_own_accounts(self, api):
        api.session.execute = AsyncMock(side_effect=[scalar_result("user123"), scalar_result(make_account_row())])

        response = api.client.get(f"{BASE}/acct-1")

        assert response.status_code == 200
        assert response.json()["id"] == "acct-1"

    def test_an_account_belonging_to_someone_else_looks_not_found(self, api):
        """Ownership is enforced by the query itself, so guessing another
        user's account id looks exactly like a nonexistent one - no 403 that
        would confirm the id is real."""
        api.session.execute = AsyncMock(side_effect=[scalar_result("user123"), scalar_result(None)])

        response = api.client.get(f"{BASE}/someone-elses-account")

        assert response.status_code == 404


class TestUpdateEndpoint:
    def test_updates_the_document_and_deletes_the_old_one(self, api, monkeypatch):
        row = make_account_row(account_document="users/user123/account/old.pdf")
        api.session.execute = AsyncMock(side_effect=[scalar_result(row)])
        delete_mock = AsyncMock()
        monkeypatch.setattr(service_module, "delete_key", delete_mock)

        response = api.client.put(
            f"{BASE}/acct-1", data={}, files={"account_document": ("new.pdf", PDF, "application/pdf")},
        )

        assert response.status_code == 200
        assert DOC_KEY in response.json()["account_document_link"]
        delete_mock.assert_awaited_once_with("users/user123/account/old.pdf")

    def test_sending_nothing_is_rejected(self, api):
        api.session.execute = AsyncMock(side_effect=[scalar_result(make_account_row())])

        response = api.client.put(f"{BASE}/acct-1", data={}, files={})

        assert response.status_code == 400

    def test_a_nonexistent_account_is_not_found(self, api):
        api.session.execute = AsyncMock(side_effect=[scalar_result(None)])

        response = api.client.put(f"{BASE}/nope", data={"account_document_id": "X"}, files={})

        assert response.status_code == 404


class TestEndpointAuth:
    def test_requires_a_token(self, api):
        del api.app.dependency_overrides[get_current_user]

        response = api.client.get(f"{BASE}/all")

        assert response.status_code in (401, 403)

    @pytest.mark.parametrize("role", ["Agent", "Admin", "FundManager", "Broker"])
    def test_other_roles_cannot_use_the_client_account_endpoints(self, api, role):
        api.app.dependency_overrides[get_current_user] = lambda: {"user_id": "user123", "role": role}

        response = api.client.get(f"{BASE}/all")

        assert response.status_code == 403
