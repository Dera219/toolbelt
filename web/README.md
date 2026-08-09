# ToolBelt — public landing site

Single self-contained `index.html`. No build step, no dependencies, no external
requests — every style and the logo are inline, so it renders identically
offline and deploys anywhere static.

## Before you deploy: fill the placeholders

Every one is marked `[LIKE THIS]` in the HTML and highlighted in yellow on the
rendered page, so nothing ships half-filled by accident.

| Placeholder | What goes there |
|---|---|
| `[LAUNCH CITY]` | The one city you're piloting in. Appears twice. |
| `[CONTACT EMAIL]` | A real monitored address. Appears five times. |
| `[LEGAL ENTITY NAME]` | Your registered business name, or your own name if sole proprietor. |
| `[BUSINESS ADDRESS]` | Required for Stripe review and most consumer-protection rules. |

```bash
cd web
grep -n '\[' index.html        # find every remaining placeholder
```

## Still missing: terms and privacy

The footer links to `/terms.html` and `/privacy.html`. **Those pages do not
exist yet**, and Stripe will follow those links during review — a 404 there is a
common cause of a rejected application.

These are legal documents, and for a marketplace they carry real weight: you are
the merchant of record, workers are independent contractors, and you handle
phone numbers and location data. I can draft both grounded in what the code
actually does, but they need review by someone qualified before you rely on
them.

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
