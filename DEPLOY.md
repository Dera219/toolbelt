# Deploying the API

The app currently runs only on your laptop. A mobile build pointed at a sleeping
machine is not a pilot, so this is the real prerequisite for Phase 4.

Everything here is prepared. What is left needs an account, which is why it is
not already done.

---

## Render (recommended)

Render reads `render.yaml` at the repo root and provisions the web service and
Postgres together. No CLI, no Docker locally.

1. Sign up at [render.com](https://render.com) with your GitHub account.
2. **New → Blueprint**, pick the `toolbelt` repo.
3. Render reads `render.yaml` and shows what it will create: `toolbelt-api`
   (Docker web service) and `toolbelt-db` (Postgres 16).
4. It prompts for the secrets marked `sync: false`:

   | Variable | Value |
   |---|---|
   | `TOOLBELT_STRIPE_SECRET_KEY` | `sk_test_…` to start. Swap to live only when you actually take money. |
   | `TOOLBELT_PAYMENTS_WEBHOOK_SECRET` | Internal fake-provider HMAC secret, **not** a Stripe value. Any long random string (`openssl rand -hex 32`). |
   | `TOOLBELT_STRIPE_WEBHOOK_SECRET` | Leave blank now; set to the `whsec_…` from webhook registration (step 2 under "After the API is up"). Blank = the route answers 503 and polling covers payout state. |
   | `TOOLBELT_PUBLIC_BASE_URL` | Leave blank now; set in step 6. |

5. Deploy. First build takes a few minutes.
6. Copy the service URL (`https://toolbelt-api.onrender.com`) into
   `TOOLBELT_PUBLIC_BASE_URL` and redeploy. **This is not optional** — startup
   fails in prod if it is still localhost, because Stripe Connect onboarding
   redirects to it and a worker sent to localhost lands nowhere.

Verify:

```bash
curl https://toolbelt-api.onrender.com/health
```

### The free tier sleeps

Render's free web services spin down after ~15 minutes idle and take ~30
seconds to wake. Fine for testing, wrong for a pilot — a customer posting a job
should not wait 30 seconds. Budget ~$7/month for the starter tier before real
users touch it. The free Postgres instance also expires after 90 days.

---

## Alternative: Fly.io

Better latency and no cold starts on the free allowance, but it is CLI-driven,
so it needs `flyctl` installed and an interactive login.

```bash
brew install flyctl && fly auth login
cd api && fly launch --no-deploy      # generates fly.toml
fly postgres create --name toolbelt-db
fly postgres attach toolbelt-db
fly secrets set TOOLBELT_ENVIRONMENT=prod \
                TOOLBELT_JWT_SECRET="$(openssl rand -hex 32)" \
                TOOLBELT_STRIPE_SECRET_KEY=sk_test_... \
                TOOLBELT_PAYMENTS_WEBHOOK_SECRET="$(openssl rand -hex 32)" \
                TOOLBELT_STRIPE_WEBHOOK_SECRET=whsec_... \
                TOOLBELT_PUBLIC_BASE_URL=https://toolbelt-api.fly.dev
fly deploy
```

---

## What deploying already accounts for

- **Migrations run before the server starts** (`alembic upgrade head` in the
  Dockerfile CMD). A failed migration exits the container and the platform keeps
  the previous release serving, rather than starting against a half-migrated
  schema.
- **`postgres://` URLs are normalized.** Render, Heroku, and Railway all hand
  out that scheme; SQLAlchemy 2 removed the alias, and plain `postgresql://`
  resolves to psycopg2, which is not a dependency. `normalize_database_url()`
  rewrites both to `postgresql+psycopg://`.
- **`create_all` no longer runs in prod.** It was a dev bootstrap that would
  have created tables outside Alembic's control and masked failed migrations.
- **Non-root container user**, and the image is Python 3.12 rather than 3.14
  because several dependencies still ship no 3.14 wheels.

## After the API is up

1. **Point the mobile app at it** — replace the placeholder hosts in
   `mobile/eas.json` (`staging.example.com`, `api.example.com`).
2. **Register the Stripe webhook** — Stripe (test mode) → Developers →
   Webhooks → add `https://<your-host>/webhooks/stripe`, listening for
   `account.updated`. Put the endpoint's signing secret (`whsec_…`) in
   `TOOLBELT_STRIPE_WEBHOOK_SECRET`. This is a latency optimization: the
   polling path (`service.sync_payout_account` → `flush_pending_payouts`)
   already flips `payouts_enabled` and releases held payouts, so a missed
   delivery costs nothing but time. Until the secret is set the route answers
   503, which shows up as delivery errors in the Stripe dashboard rather than
   silently dropped events.

   Register `/webhooks/stripe`, **not** `/webhooks/payments` — the latter
   verifies a plain HMAC in `X-Webhook-Signature` over an internal event shape
   for the fake provider in tests and would 400 every Stripe delivery.
3. **Build the Android dev client** (`mobile/BUILDS.md`) and confirm a real
   phone receives a job notification.

That third step is the first moment the whole system runs the way a user would
experience it.
