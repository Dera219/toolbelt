# ToolBelt

On-demand marketplace connecting customers with blue-collar workers — Uber for local
trades. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design and roadmap.

## Status

Phases 0–2 complete: auth, worker profiles, job posting, geo search, offers, job
lifecycle state machine, double-blind ratings, phone OTP, vetting pipeline, masked
in-app chat, and the full money loop (authorize on accept → capture on completion →
payout minus 15% fee, double-entry ledger, signed idempotent webhooks, refunds).
Payments run against a fake provider in dev/test; Stripe Connect is implemented
behind the same interface and activates when `TOOLBELT_STRIPE_SECRET_KEY` is set.

Database migrations are managed with Alembic (`cd api && .venv/bin/alembic upgrade
head`). Local Postgres + Redis: `docker compose -f infra/docker-compose.yml up -d`.

**Mobile app** (`mobile/`, Expo SDK 57 + TypeScript): one app, two modes — Hiring
(post jobs, review offers, accept & pay, chat, rate) and Working (profile + vetting,
nearby jobs, offers, start/complete, balance & payouts). Run it with the API up:

```bash
cd mobile
npm install
npx expo start
```

Scan the QR with Expo Go. On a physical device, set your machine's LAN IP in
`mobile/src/config.ts` first. In dev, phone-verification codes are readable at
`GET /dev/sms-outbox` (dev environment only).

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
cd api
.venv/bin/python -m pytest tests -q
```

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
