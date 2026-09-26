import asyncio

import pytest

from app.services.auth import login_lockout
from app.services.auth.firebase_auth.auth_errors import AccountLockedError

PHONE = "+256700000001"
IP = "203.0.113.7"


def fail(times, phone=PHONE, ip=IP):
    for _ in range(times):
        asyncio.run(login_lockout.record_failure(phone, ip))


def check(phone=PHONE, ip=IP):
    asyncio.run(login_lockout.ensure_not_locked(phone, ip))


class TestPhoneLockout:
    def test_failures_below_the_limit_do_not_lock(self, redis):
        fail(4)
        check()  # no exception

    def test_the_fifth_failure_locks_the_number(self, redis):
        fail(5)
        with pytest.raises(AccountLockedError) as exc_info:
            check()
        assert exc_info.value.status_code == 429

    def test_the_lock_message_says_how_long_to_wait(self, redis):
        fail(5)
        with pytest.raises(AccountLockedError, match="15 minutes"):
            check()

    def test_other_numbers_are_not_affected(self, redis):
        fail(5)
        check(phone="+256700000002", ip="198.51.100.1")

    def test_a_success_clears_the_phone_counter(self, redis):
        fail(4)
        asyncio.run(login_lockout.clear_failures(PHONE))
        fail(4)
        check()

    def test_the_window_is_set_once_so_it_cannot_be_extended_forever(self, redis):
        fail(1)
        key = login_lockout.phone_key(PHONE)
        redis.ttls[key] = 100  # part of the window already used
        fail(1)
        assert redis.ttls[key] == 100


class TestIpLockout:
    def test_many_failures_from_one_ip_lock_that_ip_for_every_number(self, redis):
        for i in range(20):
            fail(1, phone=f"+2567000001{i:02d}", ip=IP)

        with pytest.raises(AccountLockedError):
            check(phone="+256799999999", ip=IP)

    def test_a_different_ip_is_not_locked(self, redis):
        for i in range(20):
            fail(1, phone=f"+2567000001{i:02d}", ip=IP)
        check(phone="+256799999999", ip="198.51.100.9")

    def test_a_missing_ip_only_counts_the_phone(self, redis):
        fail(1, ip=None)
        assert list(redis.values) == [login_lockout.phone_key(PHONE)]
