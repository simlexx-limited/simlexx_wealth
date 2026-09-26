# Simlexx Wealth: how the backend works

A map of the backend for anyone (including you, next week) who needs to get
oriented fast. For what Simlexx *is*, see the [README](../README.md).

Simlexx is a savings app for clients. The banks and providers do their own KYC;
Simlexx only checks the details a client gives at sign-up. So the backend has
three kinds of people and one review step:

    client  -> signs up, sends their details       -> waits for approval
    agent   -> reviews a client's details          -> approves / rejects, with a comment
    admin   -> creates and manages agents (and other admins)

## 1. Who is who

| Role (`UserRole`) | Created by | Signs in with | Token |
|---|---|---|---|
| `User` (client) | themselves | phone + password, **or** Firebase (email/password, Google) | our JWT, or a Firebase ID token |
| `Agent` | a Simlexx admin | Firebase (email + password, or Google with the same email) | Firebase ID token |
| `Admin` | first-run setup, then another admin | phone + password | our JWT (lifetime: `ADMIN_JWT_EXPIRE_MINUTES`) |
| `Broker`, `FundManager` | (unused) | | still in the database enum, refused everywhere |

Only `User` can self-register (`SELF_REGISTRATION_ROLES`). An `Agent` account
without an `AgentProfiles` row is refused at the gate.

## 2. Every request goes through one gate

`get_current_user` in [app/services/auth/current_user.py](../app/services/auth/current_user.py).

1. Read the token header. `RS256` means a Firebase ID token; anything else is
   checked as our own JWT. Each verifier **pins its own algorithm**, so a token
   can't choose how it is checked.
2. Load the `Users` row (by `user_id` for our JWT, by `firebase_uid` for Firebase).
3. Refuse a disabled account (`status`), and an `Agent` with no profile.
4. Take the **role from the database**, never from the token, so a role change
   or a deactivation applies on the very next request.
5. Refuse an account still on a temporary password (`must_change_password`),
   except on the password-change endpoint (that one uses
   `get_current_user_any_state`).

For our own JWT there is one more check: the token carries `token_version`, and
it must match `Users.token_version`. Bumping it (password change, logout) kills
every older token.

`require_roles(...)` in [authorization.py](../app/services/auth/authorization.py)
sits on top: `Depends(require_roles(UserRole.ADMIN))`.

**First sign-in with Google or Firebase:** if no row has that `firebase_uid`,
the gate links an account created ahead of time, but only when the token's email
is *verified*, matches, and the row is a Firebase-provider account with no
`firebase_uid` yet. Phone-registered accounts can never be taken over this way.

## 3. Sign-up and sign-in flows

**Client, phone + password** (`/api/v1/auth/phone_auth`)
`register` -> OTP by SMS (and email) -> `register/confirm` -> `login` -> our JWT.
Forgot password: `login/otp` then `login/change_password`. `logout` signs out
every session.

**Client, Firebase** (`/api/v1/auth/email_auth`, `/google_signin`)
The *app* signs the person up in Firebase, then calls `register` with the
Firebase ID token as the bearer token. The email, uid and `email_verified`
come from that verified token, so the body only has `phone_number` and `role`.
`email_auth/login` exists for testing in Swagger (it calls Firebase's REST API).

**Admin**
- The first admin: `POST /api/v1/simlexx/admin/register`, open **only while no
  admin exists** (a Postgres advisory lock stops two callers both winning).
  The account is created already verified and approved (we choose our own
  admins, so there is nothing to verify), and can log in by phone right away.
  A one-time code is also sent to the admin's email only, never by SMS. There is
  no secret on this endpoint: call it yourself straight after deploying.
- More admins: `POST /simlexx/admin/admins` (a temporary password is emailed).

**Agent**
`POST /simlexx/admin/agents` creates a Firebase email/password account with a
random temporary password (Admin SDK, see
[firebase_accounts.py](../app/services/auth/firebase_auth/firebase_accounts.py)),
our `Users` row (`must_change_password = true`) and an `AgentProfiles` row, in
that order, undoing the Firebase account if our rows fail to save. The password
is emailed. The agent logs in at `email_auth/login` and must change it at
`POST /api/v1/auth/password/change` before anything else works.

## 4. Endpoint map

| Prefix | Who | What |
|---|---|---|
| `/api/v1/auth/phone_auth/*` | anyone / signed in | register, confirm, login, OTP, reset, logout |
| `/api/v1/auth/email_auth/*`, `/google_signin/*` | Firebase token | client register, email login |
| `/api/v1/auth/password/change` | any signed-in (even on a temporary password) | change your password |
| `/api/v1/more_about_user/*` | client, own profile only | `create` (multipart), `GET/PUT /{user_id}`. No delete |
| `/api/v1/reviews/*` | agent | list (`?status=&page=&limit=`), get, decide (`PUT`, comment required) |
| `/api/v1/simlexx/admin/*` | admin | `register` (first-run), `admins`, `agents` (create, list, get, `status`, `reset-password`) |
| `/api/v1/reference/countries` | anyone | countries and which are served |

## 5. The profile and review lifecycle

    client creates profile ──► pending ──► agent approves or rejects (comment required)
             ▲                               │
             └── client edits (any edit) ◄───┘   back to pending; the old comment stays visible

- `create`/`PUT` take the ID document and picture as uploads (bucket keys are
  `users/{user_id}/about/...` and `.../profile/...`).
- The client cannot set status, reviewer, comment, or file links: those fields
  don't exist on the update schema.
- A decision records the reviewer from their account (never the request), keeps
  the previous status and the comment in `SystemLogs`, syncs
  `Users.profile_verification_complete`, and emails the client. If the email
  fails, the decision still stands. A reviewer can change an earlier decision
  and cannot review their own profile.

## 6. Where things live

    app/
      routers/      thin HTTP layer: auth/, more_about_user/, reviews/, simlexx/, reference/
      services/     the logic
        auth/       current_user (the gate), authorization, login_lockout, sessions,
                    password_service, email_login_service, temp_password,
                    phone/ (our JWT + bcrypt), firebase_auth/ (Firebase helpers, errors)
        admin/  agents/  reviews/  more_about_user/  reference/  storage/
        email/  notifications/  otp_management/  sms/  logs/  rate_limiter/
      schema/       pydantic request/response models
      model/        SQLAlchemy models (models.py) and enums (mod_enum.py)
      scripts/      create_admin.py, a command-line fallback for making an admin
    alembic/versions/   migrations
    tests/              conftest.py has shared fixtures (fake Redis, the `gate` fixture)

Errors: raise a subclass of `AuthError` ([auth_errors.py](../app/services/auth/firebase_auth/auth_errors.py))
anywhere and it becomes the right HTTP response automatically.

## 7. Configuration (`.env`, see `.env.example`)

Session and security: `JWT_EXPIRE_MINUTES`, `ADMIN_JWT_EXPIRE_MINUTES`,
`OTP_TTL_SECONDS`, `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_IP_MAX_FAILED_ATTEMPTS`,
`LOGIN_LOCKOUT_SECONDS`. Who can self-register: `SELF_REGISTRATION_ROLES`
(should be just `User`). Countries: `SERVED_COUNTRIES`. Firebase:
`PATH_TO_FIREBASE` (service-account JSON), `FIREBASE_WEB_API`. Email: `SMTP_*`.
Uploads: `R2_*`, `BUCKET_URL`.

## 8. Running, testing, migrating

    docker compose up -d                                  # api, worker, postgres, redis
    source venv/bin/activate && pytest                    # all tests
    mypy .                                                # type check (CI doesn't run it, so run it yourself)
    docker compose build api worker && docker compose up -d --force-recreate api worker
    docker compose exec api alembic check                 # models vs migrations: expect "No new upgrade operations"

Swagger is at `http://localhost:8000/docs`. Use **Authorize** and paste **only**
the token string (no quotes, no `Bearer`, no `refresh_token` part).

Development OTPs: SMS is a placeholder that writes to
`docker compose exec api cat /tmp/otp_code.txt`. Admin OTPs go to email.

### Gotchas we already hit

- **Only `app/` is mounted into the containers.** Changes to `alembic/`,
  `requirements.txt` or the Dockerfile need a rebuild and a recreate.
- **The API runs `alembic upgrade head` on startup** and auto-reloads. Stop the
  `api` container before rolling migrations back, or it re-applies them.
- **Postgres stores enum *names*** (`CLIENT`, `ADMIN`), not the Python values
  (`"User"`). Adding an enum value needs `ALTER TYPE ... ADD VALUE` inside
  `op.get_context().autocommit_block()`. Autogenerate can't do it, and it also
  misses `CREATE TYPE` when an existing column becomes an enum.
- **Running Alembic from your machine** needs `DATABASE_URL` pointing at
  `localhost` (the `.env` host is `postgres`), with the password URL-encoded.
- **Rate limits:** the phone login/confirm/password endpoints allow only
  `OTP_ATTEMPT_LIMIT` requests per hour per IP. Swagger testing can hit a 429;
  clear the limiter keys in Redis, or wait. Failed-login lockout keys are
  `login_fail:*`.
- **Model annotations:** use `Mapped[str]` / `Mapped[Optional[str]]`, not
  `Mapped[String]`, or mypy complains when you assign.
- **Tests:** endpoint tests use the `gate` fixture (real tokens, fake account
  lookups) and patch the service layer. When you change access rules, break the
  rule on purpose and check a test fails (mutation check).

## 9. Not built yet / known gaps

- **ID documents are world-readable URLs** (public bucket). Move to a private
  bucket with short-lived signed URLs before real users upload IDs.
- **SMS is a placeholder** (`services/sms`); the real provider is undecided.
- **No MFA** for admins or agents, only lockout and a shorter admin session.
- **No way to deactivate an admin** (only agents). All admins are equal.
- **Not yet run against real Firebase or R2** (everything is mocked in tests).
- Session length is only configurable for admins (`ADMIN_JWT_EXPIRE_MINUTES`),
  not per role.
- `app/scripts/create_admin.py` predates the Swagger setup endpoint and is only
  a fallback; delete it if you never need it.
- `Broker` / `FundManager` enum values are unused.
