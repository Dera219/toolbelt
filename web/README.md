# ToolBelt — public landing site

Three self-contained pages — `index.html`, `terms.html`, `privacy.html`. No
build step, no dependencies, no external requests; every style and the logo are
inline, so they render identically offline and deploy anywhere static.

## Status

All placeholders are filled (08 August 2026). Operator is **Chidera Onyebu**
(sole proprietor), launch city **College Park, MD**, contact
**chideraonyebu219@gmail.com**, governing law **Maryland / Prince George's
County**.

## Open items before deploying

**1. No public postal address — deliberate, and pending.** The street address
was removed from all three pages because it is residential. A virtual business
address or PO Box is being arranged; once it exists, add it back in three
places:

- `index.html` — footer copyright line, after the operator name
- `terms.html` — §18 Contact
- `privacy.html` — §13 Contact

Give Stripe the *real* address on the account form regardless — that submission
is private and is not what was removed here.

**2. Legal values are defaults, not decisions.** These were filled with
conservative placeholders and should be confirmed:

| Value | Currently | Where |
|---|---|---|
| Liability cap | USD 100 | terms §13 |
| Message retention | 24 months | privacy §8 |
| Financial record retention | 7 years | privacy §8 |
| Data-rights response window | 30 days | privacy §9 |

**3. Terms and privacy have not been reviewed by counsel.** Each file carries a
comment block at the top saying so. The exposure is concentrated in terms §2, §6,
§12, §13 — marketplace role, contractor status, liability, indemnity.

**4. Privacy policy tracks the current data model.** It was written from the
actual SQLAlchemy columns. Adding background checks, device IDs, an analytics
SDK, or background location makes it inaccurate, which is a regulatory problem
rather than a copy problem.

## Deploying

Any static host. The two cheapest with a custom domain:

```bash
# Vercel
npx vercel --prod

# GitHub Pages — commit web/ then enable Pages on the repo
# Settings → Pages → Source: main, folder /web
```

Point a real domain at it. A `vercel.app` or `github.io` subdomain works
technically, but a business site on a free subdomain gets more scrutiny in
payment-processor review, not less.

## Accuracy notes

The copy states things the product actually does, and deliberately avoids
things it doesn't:

- **15% fee, charged on completion** — matches `platform_fee_bps: int = 1500`.
- **Authorize on accept, capture on completion** — matches the payments service.
- **Six bookable trades** — matches `LAUNCH_TRADES`.
- **Electrical, plumbing, HVAC listed as unavailable** — matches
  `REGULATED_TRADES` and `is_bookable()`. Saying this out loud is a trust signal
  rather than a gap; it tells a reviewer you understand your own licensing
  exposure.
- **No fabricated numbers.** No user counts, no testimonials, no "trusted by"
  logos. The worker names in the hero card are illustrative UI, not claimed
  customers. Do not add invented social proof — it is the fastest way to fail
  payment-processor review, and it is not true.

The example job card shows a $240–$290 move with ratings. If that reads as
implying live activity, replace the names with `Worker A/B/C`, though as a
labelled product illustration it is standard practice.
