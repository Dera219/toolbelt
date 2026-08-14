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
6. Copy the service URL **exactly as the Render dashboard shows it** into
   `TOOLBELT_PUBLIC_BASE_URL` and redeploy. **This is not optional** — startup
   fails in prod if it is still localhost, because Stripe Connect onboarding
   redirects to it and a worker sent to localhost lands nowhere.

   Do not guess the URL from the service name: `.onrender.com` subdomains are
   global, and `toolbelt-api.onrender.com` is already owned by an unrelated
   app that also calls itself "ToolBelt API". Render gives a taken name a
   suffix (`toolbelt-api-XXXX.onrender.com`).

Verify — the response must say `"environment"`, not `"version"`:

```bash
curl https://<your-service>.onrender.com/health
```

Expected: `{"status":"ok","environment":"prod"}`. A response like
`{"status":"healthy","version":"0.1.0"}` means you are looking at the
unrelated app on the unsuffixed subdomain, not this API.

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

## Current deployment

The API runs at **https://api.toolbelt.biz** (Render service `toolbelt-api`,
`srv-d9u9dgqjobas73elblp0`). The steps below describe a fresh setup; for this
project they are already done.

The custom domain is a Render custom domain plus a Cloudflare `CNAME api →
toolbelt-api-nlh2.onrender.com` set to **DNS only**. The grey cloud is
load-bearing: Cloudflare's proxy intercepts the certificate challenge, so an
orange-clouded record leaves Render stuck at "Waiting for Verification"
forever. The onrender.com host stays enabled as a fallback.

## After the API is up

1. **Point the mobile app at it** — `mobile/eas.json` now uses
   `https://api.toolbelt.biz` for both preview and production builds. There is
   no separate staging deployment; pointing preview at a host that does not
   exist means every internal build silently reaches nothing.
2. **Register the Stripe webhook** — Stripe (test mode) → Developers →
   Webhooks → add `https://<your-host>/webhooks/stripe`, listening for
   `account.updated`. *(Done: endpoint `we_1U3nGV4kwBzxDoaE7iyrcuv4` →
   `https://api.toolbelt.biz/webhooks/stripe`, secret set on Render.)*

   Worth knowing: worker accounts are created with **Accounts v2**, which does
   not emit the v1 `account.updated` event, so this endpoint will most likely
   never fire for onboarding. Payout state is kept correct by polling — that is
   the mechanism, not a fallback. See STRIPE_ONBOARDING.md. Put the endpoint's signing secret (`whsec_…`) in
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

---

## Reconciling the provider-call journal

Every money-moving call writes a `provider_calls` row before it dials Stripe and
closes it out after. A crash in between leaves the row `pending`, which honestly
means "we do not know what happened". The sweeper is what asks Stripe.

It **reads only**. It never replays a call, never moves money, and never edits a
payment or the ledger. Where Stripe and our books disagree it prints a
DISCREPANCY and stops there — see ARCHITECTURE.md §5 for why that boundary is
where it is.

### Running it

```bash
cd api

# Report only. This is the default — a first run cannot change anything.
.venv/bin/python scripts/reconcile_provider_calls.py

# Same sweep, writing the resolutions it found into the journal.
.venv/bin/python scripts/reconcile_provider_calls.py --apply

# Wider net, machine-readable.
.venv/bin/python scripts/reconcile_provider_calls.py --older-than-minutes 60 --json
```

Same work, over HTTP, for an admin who is not on a shell:

```bash
curl -X POST https://api.toolbelt.biz/admin/payments/reconcile \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"dry_run": true}'
```

Both call the same service function, so the two can never drift into
disagreeing about what a `pending` row means. `dry_run` defaults to true in both.

Rows younger than `--older-than-minutes` (default 15) are counted and left alone:
a call recorded a minute ago is probably still in flight, and judging it races
the live attempt. Losing that race means recording "nothing moved" over money
that lands a second later.

### Wiring it to a schedule

There is deliberately **no scheduler inside the app** — a background thread in a
free-tier web service that sleeps is a new failure mode, not a feature. When it
is worth automating, add a Render **Cron Job** against this same repo:

```
Command:  cd api && python scripts/reconcile_provider_calls.py --apply
Schedule: 0 * * * *
```

Give it the same `TOOLBELT_DATABASE_URL` and `TOOLBELT_STRIPE_SECRET_KEY` as the
web service. The exit code is the alerting signal:

| Exit | Meaning |
|---|---|
| `0` | Everything swept resolved cleanly. Nothing needs a human. |
| `1` | At least one row is UNKNOWN. Not urgent, but it stays pending until someone looks. |
| `2` | At least one DISCREPANCY: Stripe and our books disagree about money. Page someone. (2 wins when both are present.) |

### Reading the outcomes

| Outcome | What it means | What to do |
|---|---|---|
| **SUCCEEDED** | The call landed. Stripe holds the object, and the journal now carries its real reference. | Nothing — unless a DISCREPANCY is attached. |
| **FAILED** | Positive evidence nothing landed: the hold is still uncaptured, the charge has no such refund, no transfer carries that payment id. The customer was not charged, the worker was not paid. | Nothing. `failed` and `pending` already mean the same thing to the retry path; the row is now just truthful. Retry through the normal path if the money *should* move. |
| **UNKNOWN** | The sweeper could not tell, and refuses to guess. The row stays `pending`. | Read the detail — it names exactly what is missing. Some clear up on the next run (a payment intent still resolving at Stripe); some never will (below). |
| **DISCREPANCY** | Stripe's truth and our books disagree. Attached to any outcome; escalated, never repaired. | Act on it. The message names the payment, the job, the provider reference, the amount, and the ledger transaction key that is missing. |

The discrepancy headlines, and what each one costs if ignored:

- `MONEY TAKEN, NOT RECORDED` — the customer paid and no ledger entry says so. The
  worker is not credited.
- `WORKER PAID, NOT RECORDED` — we sent a transfer the books do not know about, so
  the worker's balance still shows money we already sent. **A second payout attempt
  would pay them twice.**
- `DOUBLE PAYOUT` / `DUPLICATE REFUNDS` / `DOUBLE CLAW-BACK` — the provider holds more
  than one object for one logical movement. Somebody was paid, refunded or collected
  from twice.
- `CUSTOMER STILL HELD` — we consider the job cancelled and Stripe still holds the
  customer's funds. Cancel the authorization.
- `ORPHANED HOLD` — a hold with no payment row behind it. Nothing in this system will
  ever capture or release it.
- `UNCAPTURABLE PAYMENT` / `RELEASE ON A CAPTURED PAYMENT` — the job's money cannot be
  collected, or a release was attempted against money already taken.
- `REFUND NOT RECORDED` / `CLAW-BACK NOT RECORDED` — the movement happened at Stripe
  and the ledger is missing its transaction.

### What it cannot determine

Stated here rather than discovered during an incident:

- **An `authorize` whose job or billing profile is gone.** An authorize's journal row
  carries no `payment_id` — by construction, the call is what justifies creating the
  payment — so the only route to Stripe is job → customer → billing profile → Stripe
  customer. Break that chain and there is no handle at all. Reported UNKNOWN.
- **An idempotency key in an older format.** The key is the only surviving description
  of an authorize. One that does not parse describes a call nothing can look up.
- **A cancelled authorization.** A cancelled PaymentIntent looks identical whether it
  held funds and was released, or was declined and then cancelled — opposite answers to
  "did the authorize succeed?". Left pending on purpose.
- **An authorization Stripe is still resolving.** Genuinely not yet knowable; the next
  run sees a settled state.
- **A refund or reversal issued by hand in the Stripe Dashboard for exactly the amount
  we asked for.** Attribution is by amount and parent object, so a manual refund of the
  same amount on the same charge is indistinguishable from ours. A *different* amount
  does not match and is safely ignored.
- **A customer with more than 100 authorizations, or a charge with more than 100
  refunds, inside the lookback window.** The reads do not paginate; a truncated page
  cannot support a "nothing exists" verdict, so the sweeper refuses to judge instead of
  answering from half the data.
