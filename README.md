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
| `TOOLBELT_PAYMENTS_WEBHOOK_SECRET` | dev-only value | HMAC key for `/webhooks/payments` |
| `TOOLBELT_PLATFORM_FEE_BPS` | `1500` | Platform take-rate in basis points |
