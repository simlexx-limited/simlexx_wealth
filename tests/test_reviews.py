import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.database.database import get_session
from app.model.mod_enum import AccountStatus, DocumentType, NotificationChannel, UserAccountStatus, UserRole
from app.routers.reviews.review_router import router
from app.schema.review_schema import ReviewDecision
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    AuthError,
    PermissionDeniedError,
    auth_error_handler,
)
from app.services.email import review_emails
from app.services.rate_limiter.limiter import limiter
from app.services.reviews import review_service
from app.services.reviews.review_service import ReviewService

BASE = "/api/v1/reviews"
PENDING, APPROVED, REJECTED = UserAccountStatus.PENDING, UserAccountStatus.APPROVED, UserAccountStatus.REJECTED
REVIEWER = {"user_id": "agent1"}


def run(coro):
    return asyncio.run(coro)


def make_profile(**overrides) -> SimpleNamespace:
    fields: dict[str, Any] = dict(
        id="profile-1", user_id="client1", first_name="Test", last_name="Client", middle_name=None,
        date_of_birth=datetime(1990, 5, 1), nationality="UG", country_of_residence="UG",
        city_of_residence="KAMPALA", full_address="plot 1", profession="ENGINEER",
        document_type=DocumentType.NATIONAL_ID, document_id="CM1",
        document_keys=["users/client1/about/doc.pdf"], document_expiry_date=date(2030, 1, 1),
        profile_picture_keys=["users/client1/profile/pic.png"], profile_verification_complete=PENDING,
        onboarding_complete=False,
        reviewer_id=None, reviewed_by=None, reviewed_at=None, review_comment=None,
        updated_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def decision(status=APPROVED, comment="Documents match") -> ReviewDecision:
    return ReviewDecision(decision=status, comment=comment)


def make_session(profile, label_row=("Ann", "Lee"), client=None) -> AsyncMock:
    """execute() answers the profile lookup first, then the reviewer-label lookup."""
    session = AsyncMock()
    session.add = MagicMock()
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = profile
    label_result = MagicMock()
    label_result.first.return_value = label_row
    session.execute = AsyncMock(side_effect=[profile_result, label_result])
    session.get = AsyncMock(return_value=client)
    return session


@pytest.fixture
def notify():
    with patch.object(review_service, "NotificationService") as notifications, \
         patch.object(review_emails, "send_profile_review_email") as email:
        notifications.return_value.create_notification = AsyncMock()
        yield SimpleNamespace(notifications=notifications.return_value, email=email)


def client_account(**overrides):
    fields = dict(id="client1", email_address="client@example.com", profile_verification_complete=PENDING)
    fields.update(overrides)
    return SimpleNamespace(**fields)


def decide(session, reviewer=None, data=None, user_id="client1"):
    return run(ReviewService(session).decide(user_id, reviewer or REVIEWER, data or decision(), "1.2.3.4", "/x"))


def logs(session):
    return [c.args[0] for c in session.add.call_args_list if hasattr(c.args[0], "action")]


class TestDecide:
    @pytest.mark.parametrize("status", [APPROVED, REJECTED])
    def test_records_the_decision_reviewer_time_and_comment(self, notify, status):
        profile = make_profile()
        session = make_session(profile, client=client_account())

        result = decide(session, data=decision(status, "Because reasons"))

        assert result.profile_verification_complete == status
        assert profile.reviewer_id == "agent1"
        assert profile.review_comment == "Because reasons"
        assert isinstance(profile.reviewed_at, datetime)
        session.commit.assert_awaited()

    def test_approving_completes_onboarding(self, notify):
        profile = make_profile()
        decide(make_session(profile, client=client_account()), data=decision(APPROVED))
        assert profile.onboarding_complete is True

    def test_rejecting_does_not_complete_onboarding(self, notify):
        profile = make_profile()
        decide(make_session(profile, client=client_account()), data=decision(REJECTED, "ID expired"))
        assert profile.onboarding_complete is False

    def test_reversing_an_earlier_approval_to_a_rejection_undoes_onboarding_too(self, notify):
        profile = make_profile(profile_verification_complete=APPROVED, onboarding_complete=True)
        decide(make_session(profile, client=client_account()), data=decision(REJECTED, "actually incomplete"))
        assert profile.onboarding_complete is False

    def test_the_reviewer_is_named_from_their_profile(self, notify):
        profile = make_profile()
        decide(make_session(profile, client=client_account()))
        assert profile.reviewed_by == "Ann Lee"

    def test_an_agent_with_no_profile_falls_back_to_their_id(self, notify):
        profile = make_profile()
        decide(make_session(profile, label_row=None, client=client_account()))
        assert profile.reviewed_by == "agent1"

    def test_the_clients_own_status_follows(self, notify):
        account = client_account()
        decide(make_session(make_profile(), client=account), data=decision(REJECTED, "Blurry photo"))
        assert account.profile_verification_complete == REJECTED

    def test_an_earlier_decision_can_be_reverted(self, notify):
        profile = make_profile(profile_verification_complete=REJECTED, review_comment="old", reviewer_id="agent9")
        session = make_session(profile, client=client_account(profile_verification_complete=REJECTED))

        decide(session, data=decision(APPROVED, "Client sent a clear scan"))

        assert profile.profile_verification_complete == APPROVED
        assert profile.reviewer_id == "agent1" and profile.review_comment == "Client sent a clear scan"
        entry = logs(session)[0]
        assert "rejected -> approved" in entry.description

    def test_the_audit_log_keeps_who_what_where_and_why(self, notify):
        session = make_session(make_profile(), client=client_account())
        decide(session, data=decision(REJECTED, "ID expired"))
        entry = logs(session)[0]
        assert entry.action == "review_profile" and entry.user_id == "agent1" and entry.entity_id == "profile-1"
        assert "pending -> rejected" in entry.description and "ID expired" in entry.description
        assert entry.ip_address == "1.2.3.4"

    def test_reviewing_your_own_profile_is_refused(self, notify):
        profile = make_profile(user_id="agent1")
        session = make_session(profile, client=client_account())
        with pytest.raises(PermissionDeniedError):
            decide(session, user_id="agent1")
        assert profile.profile_verification_complete == PENDING
        session.commit.assert_not_called()

    def test_a_missing_profile_is_a_404(self, notify):
        with pytest.raises(AccountNotFoundError):
            decide(make_session(None))


class TestNotifications:
    def test_the_client_gets_an_in_app_notification_and_an_email(self, notify):
        decide(make_session(make_profile(), client=client_account()), data=decision(REJECTED, "ID expired"))

        kwargs = notify.notifications.create_notification.await_args.kwargs
        assert kwargs["user_id"] == "client1" and kwargs["channel"] == NotificationChannel.EMAIL
        assert kwargs["notification_type"] == "profile_review" and "ID expired" in kwargs["content"]
        args = notify.email.call_args.args
        assert args == ("client@example.com", "Test", False, "ID expired")

    def test_approvals_are_worded_as_approvals(self, notify):
        decide(make_session(make_profile(), client=client_account()))
        assert notify.email.call_args.args[2] is True
        assert "approved" in notify.notifications.create_notification.await_args.kwargs["content"]

    def test_a_client_without_an_email_still_gets_the_in_app_notification(self, notify):
        decide(make_session(make_profile(), client=client_account(email_address=None)))
        notify.notifications.create_notification.assert_awaited_once()
        notify.email.assert_not_called()

    def test_a_failed_email_does_not_undo_the_decision(self, notify):
        notify.email.side_effect = RuntimeError("broker down")
        profile = make_profile()
        session = make_session(profile, client=client_account())

        result = decide(session)

        assert result.profile_verification_complete == APPROVED
        assert profile.review_comment == "Documents match"
        session.rollback.assert_not_called()

    def test_a_failed_notification_does_not_undo_the_decision_either(self, notify):
        notify.notifications.create_notification.side_effect = RuntimeError("db hiccup")
        assert decide(make_session(make_profile(), client=client_account())).profile_verification_complete == APPROVED

    def test_the_rejection_email_tells_the_client_they_can_resubmit(self):
        with patch.object(review_emails, "send_email_task") as task:
            review_emails.send_profile_review_email("c@x.com", "Ann", False, "Blurry ID")
        subject, body = task.delay.call_args.args[1:3]
        assert "needs changes" in subject
        assert "Blurry ID" in body and "reviewed again" in body

    def test_the_approval_email_carries_the_comment(self):
        with patch.object(review_emails, "send_email_task") as task:
            review_emails.send_profile_review_email("c@x.com", "Ann", True, "All good")
        assert "approved" in task.delay.call_args.args[1] and "All good" in task.delay.call_args.args[2]


class TestDecisionSchema:
    def test_a_comment_is_required_for_both_decisions(self):
        for status in ("approved", "rejected"):
            with pytest.raises(ValidationError):
                ReviewDecision(decision=status)   # type: ignore[call-arg]

    @pytest.mark.parametrize("comment", ["", "  ", "ok", " a "])
    def test_a_comment_must_say_something(self, comment):
        with pytest.raises(ValidationError):
            decision(comment=comment)

    def test_pending_is_not_a_decision(self):
        with pytest.raises(ValidationError):
            decision(status=PENDING)

    def test_the_comment_is_trimmed_and_has_a_ceiling(self):
        assert decision(comment="  fine by me  ").comment == "fine by me"
        with pytest.raises(ValidationError):
            decision(comment="x" * 1001)


class TestListing:
    def run_list(self, status=None, page=1, limit=20, total=0):
        session = AsyncMock()
        items = MagicMock()
        items.scalars.return_value.all.return_value = [make_profile()]
        count = MagicMock()
        count.scalar_one.return_value = total
        session.execute = AsyncMock(side_effect=[items, count])
        result = run(ReviewService(session).list_profiles(status, page, limit))
        return result, session

    def statements(self, session):
        return [str(c.args[0].compile(compile_kwargs={"literal_binds": True})) for c in session.execute.await_args_list]

    def test_a_status_filters_both_the_page_and_the_total(self):
        (items, total), session = self.run_list(status=PENDING, total=7)
        page_sql, count_sql = self.statements(session)
        assert "profile_verification_complete = 'PENDING'" in page_sql
        assert "profile_verification_complete = 'PENDING'" in count_sql
        assert total == 7 and len(items) == 1

    def test_no_status_means_everything(self):
        _, session = self.run_list()
        assert not any("profile_verification_complete =" in sql for sql in self.statements(session))

    def test_paging_uses_limit_and_offset(self):
        _, session = self.run_list(page=3, limit=10)
        page_sql = self.statements(session)[0]
        assert "LIMIT 10" in page_sql and "OFFSET 20" in page_sql

    def test_newest_activity_comes_first(self):
        _, session = self.run_list()
        assert "ORDER BY" in self.statements(session)[0] and "updated_at DESC" in self.statements(session)[0]

    def test_the_reviewer_label_falls_back_to_the_id(self):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
        assert run(ReviewService(session).reviewer_label("agent-x")) == "agent-x"


# ---------------------------------------------------------------- endpoints

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)
    app.dependency_overrides[get_session] = lambda: AsyncMock()
    return TestClient(app)


@pytest.fixture
def services():
    profile = make_profile()
    with patch.object(ReviewService, "list_profiles", AsyncMock(return_value=([profile], 1))) as listing, \
         patch.object(ReviewService, "get_profile", AsyncMock(return_value=profile)) as get, \
         patch.object(ReviewService, "decide", AsyncMock(return_value=make_profile(profile_verification_complete=APPROVED))) as decide_:
        yield SimpleNamespace(list=listing, get=get, decide=decide_)


BODY = {"decision": "approved", "comment": "Documents match"}
CALLS = [("get", BASE, None), ("get", f"{BASE}/client1", None), ("put", f"{BASE}/client1", BODY)]


def call(client, method, path, body=None, headers=None):
    return client.request(method.upper(), path, json=body, headers=headers or {})


class TestReviewAccess:
    @pytest.mark.parametrize("method,path,body", CALLS)
    def test_an_agent_gets_through(self, client, services, gate, method, path, body):
        assert call(client, method, path, body, gate.agent()).status_code == 200

    @pytest.mark.parametrize("method,path,body", CALLS)
    def test_nobody_else_does(self, client, services, gate, method, path, body):
        outsiders = {
            "no token": None,
            "client": gate.headers("client1", UserRole.CLIENT),
            "simlexx admin": gate.headers("boss", UserRole.ADMIN),
            "fund manager": gate.headers("f1", UserRole.FUNDMANAGER),
            "old broker role": gate.headers("b1", UserRole.BROKER),
        }
        for who, headers in outsiders.items():
            assert call(client, method, path, body, headers).status_code in (401, 403), who
        services.decide.assert_not_awaited()
        services.list.assert_not_awaited()

    def test_an_agent_without_a_profile_cannot_review(self, client, services, gate):
        assert call(client, "put", f"{BASE}/client1", BODY, gate.agent(profile=False)).status_code == 403
        services.decide.assert_not_awaited()

    def test_a_deactivated_agent_cannot_review(self, client, services, gate):
        assert call(client, "put", f"{BASE}/client1", BODY, gate.agent(status=AccountStatus.DISABLE)).status_code == 403

    def test_an_agent_on_a_temporary_password_cannot_review(self, client, services, gate):
        assert call(client, "put", f"{BASE}/client1", BODY, gate.agent(must_change_password=True)).status_code == 403


class TestReviewEndpoints:
    def test_the_list_is_paged_and_can_be_filtered_by_status(self, client, services, gate):
        response = call(client, "get", f"{BASE}?status=pending&page=2&limit=5", headers=gate.agent())
        assert services.list.await_args.args == (PENDING, 2, 5)
        body = response.json()
        assert body["total"] == 1 and body["page"] == 2 and body["limit"] == 5
        assert body["items"][0]["user_id"] == "client1"

    def test_no_filter_means_everything(self, client, services, gate):
        call(client, "get", BASE, headers=gate.agent())
        assert services.list.await_args.args == (None, 1, 20)

    @pytest.mark.parametrize("query", ["status=deleted", "page=0", "limit=0", "limit=101"])
    def test_bad_query_values_are_422(self, client, services, gate, query):
        assert call(client, "get", f"{BASE}?{query}", headers=gate.agent()).status_code == 422

    def test_the_decision_uses_the_agent_from_the_token(self, client, services, gate):
        response = call(client, "put", f"{BASE}/client1",
                        {**BODY, "reviewer_id": "someone-else", "reviewed_by": "Fake Bank"},
                        gate.agent("agent-7"))

        assert response.status_code == 200
        args, kwargs = services.decide.await_args
        assert args[0] == "client1"
        assert kwargs["reviewer"]["user_id"] == "agent-7"
        assert kwargs["data"].decision == APPROVED and kwargs["data"].comment == "Documents match"
        assert "reviewer_id" not in kwargs["data"].model_dump()

    @pytest.mark.parametrize("body", [
        {"decision": "approved"},
        {"comment": "no decision"},
        {"decision": "pending", "comment": "still thinking"},
        {"decision": "approved", "comment": "ok"},
        {"decision": "maybe", "comment": "unsure about it"},
    ])
    def test_invalid_decisions_are_422(self, client, services, gate, body):
        assert call(client, "put", f"{BASE}/client1", body, gate.agent()).status_code == 422
        services.decide.assert_not_awaited()

    def test_the_response_shows_the_decision(self, client, services, gate):
        body = call(client, "put", f"{BASE}/client1", BODY, gate.agent()).json()
        assert body["profile_verification_complete"] == "approved"
        assert "review_comment" in body and "reviewed_by" in body
