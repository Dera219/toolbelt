# Stripe Onboarding — website + business description

## First: you may not need any of this yet

Stripe gives you **test-mode API keys as soon as the account exists**, before
business verification. Test mode is a full simulation — real API calls, real
webhooks, real Connect flows, no real money and no activation requirements.

The immediate goal is the Phase 2 exit criterion, *"real $1 test transaction end
to end."* That runs entirely in test mode. **Grab `sk_test_...` from the
dashboard and the integration work can start today**, in parallel with
activation rather than behind it.

Fill in the business profile below when you want to move real money — i.e.
before the Phase 4 pilot, not before the Phase 2 integration.

---

## Business summary (paste-ready)

### Short version — one-liner

> ToolBelt is an on-demand marketplace that connects customers with local
> blue-collar service workers for home and property jobs such as cleaning,
> moving, handyman work, furniture assembly, yard work, and painting.

### Standard version — "describe your business"

> ToolBelt is a two-sided marketplace for local home and property services.
> Customers post a job (for example a move, a house cleaning, or furniture
> assembly), nearby vetted workers send offers with a price, and the customer
> accepts the offer they want.
>
> Payment is authorized when the customer accepts an offer and captured only
> when the job is marked complete. ToolBelt retains a 15% platform fee and pays
> the remaining 85% out to the worker. Workers are independent contractors, not
> employees, and are onboarded through Stripe Connect with their own connected
> accounts.
>
> We launch with unlicensed trades only — cleaning, moving, handyman, assembly,
> yard work, and painting. Licensed trades such as electrical, plumbing, and
> HVAC are blocked in the product until a license-verification pipeline is in
> place.

### Connect-specific version

Stripe scrutinizes marketplaces more closely than single-merchant businesses,
because money flows to third parties. Answer these directly:

> **Who are your sellers?** Independent local service workers (contractors) who
> complete home and property jobs. Each is onboarded as a Stripe Connect
> connected account and completes Stripe's own identity verification.
>
> **Who are your buyers?** Individual consumers and small property owners
> booking a specific job at an agreed price.
>
> **How do funds flow?** The customer's card is authorized when they accept a
> worker's offer. Funds are captured when the job is marked complete by both
> sides. ToolBelt takes a 15% platform fee and transfers the balance to the
> worker's connected account. Every movement is recorded in a double-entry
> ledger.
>
> **What is the fulfillment timeline?** Same-day to roughly two weeks. Jobs are
> typically completed within days of booking; funds are only captured after
> completion, so customers are not charged for undelivered work.
>
> **What is your refund policy?** Full or partial refunds are issued through the
> platform when a job is cancelled before completion or when a dispute is
> resolved in the customer's favor. Refunds are supported natively in the
> payments module.
>
> **How do you prevent fraud and abuse?** Phone verification via OTP, a worker
> vetting pipeline with explicit approval states, double-blind ratings, masked
> in-app messaging so contact details are not exchanged before booking, and
> object-level authorization on every endpoint.

---

## The website requirement

Stripe wants a URL that lets a reviewer see what you sell, at what price, and
under what terms. **There is currently no deployed ToolBelt site** — the mobile
app is an unpublished Expo build and the repo is private, so neither works as a
submission.

Three options, in order of how well they hold up:

1. **A one-page public landing site.** The reliable answer, and cheap — a static
   page describing the service, the trades covered, how pricing and the 15% fee
   work, plus terms, privacy policy, and a contact address. Deployable free on
   GitHub Pages, Vercel, or Netlify with a real domain pointed at it.
2. **App store listing.** Legitimate once the Phase 3 build ships, but that is
   downstream of TestFlight and cannot unblock anything now.
3. **Description in lieu of a site.** Stripe does accept a detailed written
   description when no site exists yet. It works more often for a conventional
   business than for a marketplace moving money to third parties, so treat it as
   the fallback rather than the plan.

**Recommendation:** option 1. A landing page is needed for the pilot regardless
— workers and customers will both look for one — so it is not throwaway work.

## What else activation will ask for

Have these ready; none are things I can supply:

- Legal entity type and business name. A sole proprietorship under your own name
  is acceptable to start, though an LLC is worth having before real money moves,
  for liability reasons that matter in a trades marketplace specifically.
- Tax ID — SSN for a sole proprietorship, EIN for an entity.
- A business bank account for platform-fee payouts.
- A statement descriptor: the text on customer card statements. Keep it
  recognizable — `TOOLBELT` — since an unrecognizable descriptor is a leading
  cause of chargebacks.
- Estimated monthly volume and average transaction size. Estimate honestly and
  low for a pre-launch pilot; understating is fine, overstating triggers review.

## One thing to decide before going live

Whether ToolBelt is the **merchant of record**. Under the current design it is:
the customer pays ToolBelt, and ToolBelt pays the worker. That means chargebacks
land on you, not on the worker.

That is the normal structure for this model and the right default, but it is a
real liability that should be priced into the 15% and covered explicitly in the
terms of service. Worth a conversation with a lawyer before the pilot, not
before the test transaction.
