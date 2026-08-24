# ToolBelt

An on-demand marketplace connecting customers with blue-collar workers — job
posting, geo matching, offers, a job lifecycle state machine, masked in-app
chat, double-blind ratings, and a full money loop with a double-entry ledger.
FastAPI + Postgres on the server, one Expo app for iOS, Android and web.

**Live:** the API is deployed at [api.toolbelt.biz](https://api.toolbelt.biz/health)
and the web client at [app.toolbelt.biz](https://app.toolbelt.biz). Both are on
free instances that sleep when idle, so the first request can take 50 seconds.

**225 API tests, 206 mobile tests across four platforms, CI on both plus a real
Postgres.** The money loop has been exercised against live Stripe rather than
only against the fake provider.

## What is actually finished, and what is not

Honesty about this is cheaper than being caught by it, so:

**Working end to end.** Auth including social sign-in, worker profiles, job
posting and geo search, offers, the job lifecycle, in-app chat, ratings, and the
money path — authorize on accept, capture on completion, payout minus a 15% fee,
double-entry ledger, signed idempotent webhooks, refunds with transfer reversal.
Every provider call goes through a journal that records intent before the call,
so a crash mid-payment is recoverable rather than ambiguous.

**Blocked, and worth stating plainly: a worker cannot finish signup on the live
API.** Vetting requires a verified phone, phone verification requires SMS, and
the Twilio account behind it is on a trial tier that only permits predefined
message templates. The integration is written and tested — a restricted API key
authenticates and Twilio accepts the request — and it stops at that policy. It
needs a paid Twilio account, not more code.

**Built but never observed: Android push.** The Firebase project, the FCM V1
credentials and the APK all exist and every link upstream of the handset is
verified. No notification has ever actually arrived on a phone, because there
is no Android device to receive one. Treat it as plausible, not working.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the system design and roadmap, and
[DEPLOY.md](DEPLOY.md) for the deployment. Database migrations are managed with
Alembic (`cd api && .venv/bin/alembic upgrade head`); local Postgres and Redis
come up with `docker compose -f infra/docker-compose.yml up -d`.

**Mobile app** (`mobile/`, Expo SDK 57 + TypeScript): one app, two modes — Hiring
(post jobs, review offers, accept & pay, chat, rate) and Working (profile + vetting,
nearby jobs, offers, start/complete, balance & payouts). Run it with the API up:

```bash
cd mobile
npm install
npx expo start
```

Scan the QR with Expo Go. No configuration is needed on a physical device —
`src/config.ts` derives the API host from the Metro server Expo already told the
app about. Set `EXPO_PUBLIC_API_URL` only to point somewhere else deliberately.

Expo Go cannot receive push notifications (removed in SDK 53); that needs a real
build. See [mobile/BUILDS.md](mobile/BUILDS.md), which also documents the two
traps that make push look broken when it is not.

In dev, phone-verification codes are readable at `GET /dev/sms-outbox`.

## Run the API

```bash
cd api
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Interactive API docs: http://127.0.0.1:8000/docs

## Run the tests

```bash
cd api && .venv/bin/python -m pytest -q
```

```bash
cd mobile && npm test
```

The mobile suite runs under four Jest projects — iOS, Android, web and node —
because `src/payments/sheet.ts` and `sheet.native.ts` are resolved by platform,
and the web variant is the only thing keeping Stripe's native SDK out of the web
bundle. A single-platform run would not notice if that broke. CI runs both
suites, and the API suite a second time against a real Postgres.

## Configuration

Environment variables (prefix `TOOLBELT_`, or an `api/.env` file):

| Variable | Default | Notes |
|---|---|---|
| `TOOLBELT_DATABASE_URL` | `sqlite:///./toolbelt.db` | Postgres in prod |
| `TOOLBELT_JWT_SECRET` | dev-only value | **Required in prod**, ≥32 random bytes |
| `TOOLBELT_ENVIRONMENT` | `dev` | `dev` / `test` / `prod` |
| `TOOLBELT_STRIPE_SECRET_KEY` | unset | Enables the real Stripe provider |
| `TOOLBELT_STRIPE_PUBLISHABLE_KEY` | unset | Returned to the app for the native payment sheet; public by design |
| `TOOLBELT_PAYMENTS_WEBHOOK_SECRET` | dev-only value | HMAC key for `/webhooks/payments` |
| `TOOLBELT_PLATFORM_FEE_BPS` | `1500` | Platform take-rate in basis points |
| `TOOLBELT_WEB_APP_ORIGINS` | unset | Browser origins allowed cross-origin. Unset = no browser client. Never a wildcard |
| `TOOLBELT_TWILIO_ACCOUNT_SID` | unset | Always required for SMS; it is in the request path, not the credentials |
| `TOOLBELT_TWILIO_API_KEY_SID` / `_SECRET` | unset | Preferred credential — revocable alone, restrictable to creating Messages |
| `TOOLBELT_TWILIO_AUTH_TOKEN` | unset | Alternative to the key pair. Can do everything; rotating it breaks every integration |
| `TOOLBELT_TWILIO_MESSAGING_SERVICE_SID` | unset | Preferred sender in prod — owns the number pool and opt-out handling |
| `TOOLBELT_TWILIO_FROM_NUMBER` | unset | Alternative sender. Fine for a trial account |

Without SMS credentials the API still starts and logs an error: jobs, offers and
payments all work, and only phone verification fails. Refusing to boot would
trade a broken signup for an outage.

## Social sign-in setup

Client IDs are public identifiers, not secrets — the server verifies every token
against the provider independently, so a wrong value fails closed.

**Google** (free, ~10 minutes): [console.cloud.google.com](https://console.cloud.google.com)
→ APIs & Services → Credentials → Create OAuth client ID → **Web application**.
Add `http://localhost:8081` as both an authorized JavaScript origin and a
redirect URI. Then:

```bash
# api/.env
TOOLBELT_GOOGLE_CLIENT_IDS=<client-id>.apps.googleusercontent.com

# mobile/.env
EXPO_PUBLIC_GOOGLE_CLIENT_ID=<client-id>.apps.googleusercontent.com
```

Restart both servers. `GET /auth/providers` lists what the app will show; a
provider with no client ID is hidden and refuses tokens (501).

**Microsoft**: same shape, registered at [entra.microsoft.com](https://entra.microsoft.com).
No domain or payment needed for development.

**Apple**: requires the $99/yr Apple Developer Program. Native iOS needs only a
bundle ID; the web flow additionally needs a verified domain.

Notes:
- The implicit ID-token flow is used deliberately (a mobile client cannot hold a
  client secret). PKCE must stay **off** for it — `code_challenge_method` is only
  valid for the code flow and providers reject the request outright.
- RS256 verification needs `PyJWT[crypto]`. Without it every sign-in fails with a
  generic "invalid token"; the API now refuses to start in that state.

## Card entry and payouts

Card details never reach this server. Native builds open Stripe's PaymentSheet;
the web build uses Stripe's hosted card page, because `@stripe/stripe-react-native`
has no browser implementation — see `mobile/src/payments/`. Both return a
reference to the completed setup, which the server resolves against Stripe
before storing anything.

Two traps worth knowing:

- **Saved payment methods are not always cards.** Stripe Checkout may save a
  *Link* payment method. Filtering lookups by `type="card"` makes a successful
  save look like a failure.
- **Worker payouts need Connect enabled** on the Stripe account, with the
  **Marketplace** business model. See [STRIPE_ONBOARDING.md](STRIPE_ONBOARDING.md)
  for the exact steps and the sandbox/key-pairing pitfalls.

Test cards: `4242 4242 4242 4242`, any future expiry, any CVC.

## Database

Development and production both run **Postgres**. Start it with
`docker compose -f infra/docker-compose.yml up -d`, or locally:

```bash
brew install postgresql@16 && brew services start postgresql@16
createdb -O toolbelt toolbelt
cd api && .venv/bin/alembic upgrade head
```

Then set in `api/.env`:

```
TOOLBELT_DATABASE_URL=postgresql+psycopg://toolbelt:toolbelt-dev-only@localhost:5432/toolbelt
```

The test suite defaults to SQLite for speed; point it at Postgres with
`TOOLBELT_TEST_DATABASE_URL=…` (CI runs both).

**Why this matters:** the app creates tables with `create_all()` at startup, so a
missing migration is invisible in development while breaking any deployment
built from migrations. That happened — `alembic/env.py` did not import the trust
models, so the `disputes` table was never in a migration. `tests/test_migrations.py`
now builds a database from migrations alone and fails if it does not match the
models. Any deployed database created before this fix needs
`alembic upgrade head` to gain the disputes table.

To copy an old SQLite dev database into Postgres:

```bash
cd api && .venv/bin/python scripts/sqlite_to_postgres.py \
  "sqlite:///./toolbelt.db" \
  "postgresql+psycopg://toolbelt:toolbelt-dev-only@localhost:5432/toolbelt"
```
