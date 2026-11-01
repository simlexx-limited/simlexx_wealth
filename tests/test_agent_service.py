import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy

from app.model.mod_enum import AccountStatus, AuthProvider, UserRole
from app.schema.agent_schema import AgentCreate
from app.services.agents import agent_service
from app.services.agents.agent_service import AgentService, agent_response, mask_email
from app.services.auth.firebase_auth import firebase_accounts
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    AuthError,
    DuplicateAccountError,
    EmailNotQueuedError,
)
from app.services.email import agent_emails


def run(coro):
    return asyncio.run(coro)


def data(**overrides) -> AgentCreate:
    fields = dict(first_name="Jane", last_name="Doe", email_address="Jane@Simlexx.com",
                  phone_number="+256700000010", job_title="Verifier")
    fields.update(overrides)
    return AgentCreate(**fields)


def make_session(existing_rows=None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.all.return_value = existing_rows or []
    session.execute = AsyncMock(return_value=result)

    async def flush():
        for call in session.add.call_args_list:
            obj = call.args[0]
            if getattr(obj, "id", None) is None and hasattr(obj, "role"):
                obj.id = "new-user"

    async def refresh(obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)

    session.flush = AsyncMock(side_effect=flush)
    session.refresh = AsyncMock(side_effect=refresh)
    return session


def added(session, kind):
    return [c.args[0] for c in session.add.call_args_list if type(c.args[0]).__name__ == kind]


@pytest.fixture
def parts():
    with patch.object(firebase_accounts, "create_email_account", AsyncMock(return_value="uid-9")) as create, \
         patch.object(firebase_accounts, "delete_account", AsyncMock()) as delete, \
         patch.object(agent_emails, "send_account_created_email") as email:
        yield SimpleNamespace(create=create, delete=delete, email=email)


class TestCreateAgent:
    def test_creates_the_account_and_profile(self, parts):
        session = make_session()

        user, profile, queued, temporary_password = run(AgentService(session).create_agent(data(), created_by="boss"))

        assert queued is True
        assert temporary_password
        assert user.role == UserRole.AGENT
        assert user.auth_provider == AuthProvider.FIREBASE_SIMPLE
        assert user.firebase_uid == "uid-9"
        assert user.email_address == "jane@simlexx.com"          # lower-cased
        assert user.must_change_password is True
        assert user.status == AccountStatus.ACTIVE
        assert (profile.first_name, profile.last_name, profile.job_title) == ("Jane", "Doe", "Verifier")
        assert profile.created_by == "boss"
        session.commit.assert_awaited()   # the account, then the audit log

    def test_the_same_temporary_password_goes_to_firebase_and_the_email(self, parts):
        run(AgentService(make_session()).create_agent(data(), created_by="boss"))

        firebase_password = parts.create.await_args.args[1]
        assert parts.email.call_args.kwargs["temporary_password"] == firebase_password
        assert parts.email.call_args.kwargs["to_address"] == "jane@simlexx.com"
        assert parts.email.call_args.kwargs["account_label"] == "agent"
        assert len(firebase_password) >= 12

    def test_the_password_is_never_stored_or_logged(self, parts):
        session = make_session()
        run(AgentService(session).create_agent(data(), created_by="boss"))

        password = parts.create.await_args.args[1]
        assert added(session, "Users")[0].password is None
        log = added(session, "SystemLogs")[0]
        assert password not in log.description
        assert log.action == "create_agent" and log.user_id == "boss" and log.entity_id == "new-user"

    def test_a_duplicate_is_refused_before_anything_is_created(self, parts):
        session = make_session(existing_rows=[("jane@simlexx.com", "+256700000099")])
        with pytest.raises(DuplicateAccountError, match="email"):
            run(AgentService(session).create_agent(data(), created_by="boss"))
        parts.create.assert_not_awaited()
        session.add.assert_not_called()

    def test_duplicate_phone_and_both(self, parts):
        with pytest.raises(DuplicateAccountError, match="phone"):
            run(AgentService(make_session([("x@y.com", "+256700000010")])).create_agent(data(), created_by="b"))
        with pytest.raises(DuplicateAccountError, match="Both"):
            run(AgentService(make_session([("jane@simlexx.com", "+256700000010")])).create_agent(data(), created_by="b"))

    def test_a_firebase_failure_creates_nothing_locally(self, parts):
        parts.create.side_effect = DuplicateAccountError("This email address is already registered")
        session = make_session()
        with pytest.raises(DuplicateAccountError):
            run(AgentService(session).create_agent(data(), created_by="boss"))
        session.add.assert_not_called()
        parts.email.assert_not_called()

    def test_a_database_failure_removes_the_firebase_account(self, parts):
        session = make_session()
        session.commit.side_effect = RuntimeError("db down")
        with pytest.raises(RuntimeError):
            run(AgentService(session).create_agent(data(), created_by="boss"))
        session.rollback.assert_awaited()
        parts.delete.assert_awaited_once_with("uid-9")
        parts.email.assert_not_called()

    def test_a_race_on_a_unique_column_is_a_409_and_cleans_up(self, parts):
        session = make_session()
        session.commit.side_effect = sqlalchemy.exc.IntegrityError("x", {}, Exception("dup"))
        with pytest.raises(DuplicateAccountError):
            run(AgentService(session).create_agent(data(), created_by="boss"))
        parts.delete.assert_awaited_once_with("uid-9")

    def test_a_failed_cleanup_does_not_hide_the_real_error(self, parts):
        parts.delete.side_effect = RuntimeError("firebase down too")
        session = make_session()
        session.commit.side_effect = ValueError("the real error")
        with pytest.raises(ValueError, match="real error"):
            run(AgentService(session).create_agent(data(), created_by="boss"))

    def test_an_email_that_cannot_be_queued_still_creates_the_account(self, parts):
        parts.email.side_effect = RuntimeError("broker down")
        session = make_session()

        user, _, queued, _ = run(AgentService(session).create_agent(data(), created_by="boss"))

        assert queued is False and user.firebase_uid == "uid-9"
        assert "NOT queued" in added(session, "SystemLogs")[0].description


def agent_session(user, profile):
    session = make_session()
    found = MagicMock()
    found.first.return_value = (user, profile)
    found.all.return_value = [(user, profile)]
    session.execute = AsyncMock(return_value=found)
    return session


def make_target(**overrides):
    fields = dict(id="target", role=UserRole.AGENT, status=AccountStatus.ACTIVE, firebase_uid="uid-t",
                  email_address="target@simlexx.com", phone_number="+256700000020", must_change_password=False)
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_profile(**overrides):
    fields = dict(user_id="target", first_name="Tom", last_name="Lee", job_title=None,
                  created_at=datetime.now(timezone.utc))
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestReading:
    def test_list_and_get(self):
        session = agent_session(make_target(), make_profile())
        listed = run(AgentService(session).list_agents())
        assert [a.user_id for a in listed] == ["target"] and listed[0].first_name == "Tom"
        assert run(AgentService(session).get_agent("target")).last_name == "Lee"

    def test_an_unknown_agent_is_a_404(self):
        session = make_session()
        session.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
        with pytest.raises(AccountNotFoundError):
            run(AgentService(session).get_agent("nobody"))

    def test_the_response_has_no_secrets(self):
        response = agent_response(make_target(), make_profile())
        assert "password" not in " ".join(response.model_dump()).replace("must_change_password", "")


class TestSetStatus:
    @pytest.fixture
    def side_effects(self):
        with patch.object(firebase_accounts, "set_account_disabled", AsyncMock()) as fb, \
             patch.object(agent_emails, "send_account_status_email") as mail:
            yield SimpleNamespace(firebase=fb, mail=mail)

    def test_deactivating(self, side_effects):
        target = make_target()
        session = agent_session(target, make_profile())

        result = run(AgentService(session).set_status("target", False, "left the team", "boss"))

        assert target.status == AccountStatus.DISABLE and result.status == AccountStatus.DISABLE
        side_effects.firebase.assert_awaited_once_with("uid-t", True)
        assert side_effects.mail.call_args.args[2] is False
        log = added(session, "SystemLogs")[0]
        assert log.action == "deactivate_agent" and "left the team" in log.description and log.user_id == "boss"

    def test_reactivating(self, side_effects):
        target = make_target(status=AccountStatus.DISABLE)
        run(AgentService(agent_session(target, make_profile())).set_status("target", True, None, "boss"))
        assert target.status == AccountStatus.ACTIVE
        side_effects.firebase.assert_awaited_once_with("uid-t", False)

    def test_a_firebase_failure_does_not_undo_the_deactivation(self, side_effects):
        side_effects.firebase.side_effect = RuntimeError("firebase down")
        target = make_target()
        run(AgentService(agent_session(target, make_profile())).set_status("target", False, None, "boss"))
        assert target.status == AccountStatus.DISABLE

    def test_an_unknown_agent_is_a_404_and_nothing_changes(self, side_effects):
        session = make_session()
        session.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
        with pytest.raises(AccountNotFoundError):
            run(AgentService(session).set_status("nobody", False, None, "boss"))
        side_effects.firebase.assert_not_awaited()


class TestResetPassword:
    @pytest.fixture
    def side_effects(self):
        with patch.object(firebase_accounts, "make_password_reset_link", AsyncMock(return_value="https://reset/one-time")) as link, \
             patch.object(firebase_accounts, "revoke_account_sessions", AsyncMock()) as revoke, \
             patch.object(agent_emails, "send_password_reset_email") as mail:
            yield SimpleNamespace(link=link, revoke=revoke, mail=mail)

    def test_emails_the_link_and_never_returns_it(self, side_effects):
        session = agent_session(make_target(), make_profile())

        result = run(AgentService(session).reset_password("target", "boss"))

        assert "https://reset/one-time" not in str(result)
        assert result["message"].endswith("t***@simlexx.com")
        assert side_effects.mail.call_args.args == ("target@simlexx.com", "Tom", "https://reset/one-time")

    def test_ends_the_agents_sessions(self, side_effects):
        run(AgentService(agent_session(make_target(), make_profile())).reset_password("target", "boss"))
        side_effects.revoke.assert_awaited_once_with("uid-t")

    def test_is_logged_with_the_actor(self, side_effects):
        session = agent_session(make_target(), make_profile())
        run(AgentService(session).reset_password("target", "boss"))
        log = added(session, "SystemLogs")[0]
        assert log.action == "reset_agent_password" and log.user_id == "boss" and log.entity_id == "target"

    def test_an_account_without_email_sign_in_cannot_be_reset(self, side_effects):
        with pytest.raises(AuthError):
            run(AgentService(agent_session(make_target(firebase_uid=None), make_profile())).reset_password("target", "boss"))
        side_effects.link.assert_not_awaited()

    def test_an_email_that_cannot_be_queued_is_reported(self, side_effects):
        side_effects.mail.side_effect = RuntimeError("broker down")
        with pytest.raises(EmailNotQueuedError):
            run(AgentService(agent_session(make_target(), make_profile())).reset_password("target", "boss"))


def test_mask_email():
    assert mask_email("jane.doe@simlexx.com") == "j***@simlexx.com"


def test_module_exposes_the_response_builder():
    assert agent_service.agent_response(make_target(), make_profile()).user_id == "target"
