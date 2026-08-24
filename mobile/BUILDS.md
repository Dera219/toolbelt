# Builds & push notifications

## The constraint that decides everything here

**Remote push notifications do not work in Expo Go.** Support was removed in
SDK 53, and this project is on SDK 57 — the SDK 57 docs still say it plainly:
push "is unavailable in Expo Go on Android from SDK 53. A development build is
required." Scanning the QR code with Expo Go runs the app fine, but a device
registered that way will never receive a job alert and `registerForPush()`
returns null.

Testing push therefore requires a **real binary** containing the native
notification module.

| Target | Cost | Gets you |
|---|---|---|
| Expo Go | free | The app, no push. Fine for UI and flow work. |
| Android build | **free** | Real push, installable APK, no store account |
| iOS build / TestFlight | **$99/yr** Apple Developer | Real push on iPhone, TestFlight distribution |

**Android first is the cheap path.** It costs nothing, exercises the entire push
system end to end, and needs no Apple relationship. Do that before spending $99.

## Two traps that make push look broken when it is not

Read these before you build. Each one produces silence rather than an error,
and each one has cost someone an afternoon.

### 1. A local API never sends a push. It only logs one.

`api/app/modules/notifications/push.py` picks the sender by environment:

```python
def get_push_sender() -> PushSender:
    settings = get_settings()
    if settings.environment == "prod":
        return ExpoPushSender()
    return _dev_sender          # DevPushSender: appends to an outbox, logs, returns
```

`TOOLBELT_ENVIRONMENT` is `prod` **only on Render** (`render.yaml`). On your
laptop it is `dev`, so every notification the API decides to send is written to
a log line and dropped. The phone is not at fault and neither is FCM.

**Consequence: point the build at `https://api.toolbelt.biz`.** That is the only
deployment that actually calls Expo's push service. Testing push against
localhost cannot succeed, however correct everything else is.

### 2. Android push needs FCM credentials that EAS will not create for you

EAS generates an Android keystore on its own. It does **not** generate Firebase
credentials — you supply those from your own Firebase project, and without them
Android push fails. There are two separate artifacts and they are not equally
secret:

| Artifact | What it is | Where it goes | Committed? |
|---|---|---|---|
| `google-services.json` | Public identifiers (sender id, app id) | `mobile/google-services.json`, referenced by `android.googleServicesFile` | **Yes** — no secrets in it, and the build needs it |
| Service account key JSON | A **private key** that can send push as you | Uploaded to EAS once, then kept out of the repo | **Never** — `.gitignore` already blocks the usual filenames |

Both must come from the *same* Firebase project. A mismatched pair is what
produces `MismatchSenderId` on send, with a perfectly healthy-looking token on
the device.

## Step 1 — Expo account and login (you, interactively)

```bash
npx eas-cli login
```

Free account; create one at expo.dev first if you do not have one. The session
is stored in `~/.expo`, so everything after this is non-interactive.

## Step 2 — Firebase (you, in a browser)

1. Firebase console → create a project (or reuse one). Analytics is optional
   and irrelevant here.
2. Add an **Android** app. The package name must be exactly
   **`com.toolbelt.mobile`** — it is what `app.json` declares, and a mismatch
   breaks delivery silently.
3. Download `google-services.json` → save it to `mobile/google-services.json`.
4. Project settings → **Service accounts** → *Generate new private key*. Save
   the JSON outside the repo (`~/Downloads` is fine); it is a credential.

## Step 3 — Wire it up

Add the file reference to `app.json` under `android` — this is what enables FCM
in the build:

```json
"android": {
  "package": "com.toolbelt.mobile",
  "googleServicesFile": "./google-services.json",
  ...
}
```

Link the EAS project (writes `extra.eas.projectId`, which `src/push.ts` reads
when it asks for a token), then upload the service account key:

```bash
npx eas-cli init
npx eas-cli credentials
```

In `credentials`: **Android → production → Google Service Account → Manage your
Google Service Account Key for Push Notifications (FCM V1) → upload**, and point
it at the file from step 2.4. The key is per-project, so one upload covers every
build profile.

## Step 4 — Build the APK

```bash
npx eas-cli build --profile preview --platform android
```

**Use `preview`, not `development`, for testing push.** The difference matters:

- `preview` — a standalone APK with the JS bundled in and
  `EXPO_PUBLIC_API_URL=https://api.toolbelt.biz` baked at build time. Install it
  and it works on its own: no laptop, no Metro, no shared wifi, and it talks to
  the one API that really sends push.
- `development` — `developmentClient: true`, so the app loads its JS from Metro
  on your machine at runtime. Right for iterating on code, wrong for a push
  test: the API URL comes from your local environment rather than from
  `eas.json`, and the obvious local choice is the API that cannot send.

EAS builds in the cloud and returns an install URL. Open it on the Android phone
and install; you will have to allow installs from that browser.

Budget the time honestly: the free plan allows **15 Android builds a month** and
puts them in the **low-priority queue**, where peak-hour waits reach 90+ minutes
before the build itself starts. The build is ~15 minutes; the queue is not. Free
plan builds also time out at 45 minutes.

For the development build later, when you do want the fast edit loop:

```bash
npx eas-cli build --profile development --platform android
npx expo start --dev-client
```

Its API URL resolves through `src/config.ts`, which already derives your
machine's LAN address from the Metro host — that is why the `development`
profile deliberately sets no `EXPO_PUBLIC_API_URL`. Override it explicitly if
you want that build against production:

```bash
EXPO_PUBLIC_API_URL=https://api.toolbelt.biz npx expo start --dev-client
```

## Verifying push without owning an Android phone

An **Android emulator running a Google Play services image can receive FCM push**
— it registers normally. The only thing stopping it is our own guard:
`registerForPush()` returns null on `!Device.isDevice`, which is correct for an
iOS Simulator (genuinely cannot receive remote notifications) and wrong for an
Android emulator.

So there is a deliberate escape hatch, behind two locks:

```bash
EXPO_PUBLIC_ALLOW_EMULATOR_PUSH=1 npx expo start --dev-client
```

It requires `__DEV__` **and** the variable. A release build ignores it entirely
even if the variable is set — a shipped app registering tokens for devices that
cannot receive anything would be a real defect, and there are tests for both
locks.

You still need a Play-services emulator image: in Android Studio's Device
Manager, pick a system image whose target says **"Google Play"**, not "Google
APIs" and not the plain AOSP one. Without Play services there is no FCM to
register with, and the token request simply fails.

This is the cheapest way to prove the chain end to end. It is not a substitute
for a real handset before shipping — delivery on physical devices involves
battery optimisation and Doze behaviour an emulator never exercises.

## Verifying push actually works

The live API is on Render's free tier and **spins down when idle — the first
request can take 50+ seconds.** Hit https://api.toolbelt.biz/health once and
wait for it before deciding anything is broken.

1. Sign up / log in on the phone. `registerForPush()` runs on login, asks for
   permission, and POSTs the token to `/me/device-tokens`.
2. Make that account a **worker**, set its trade and service area, and set its
   `vetting_status` to `verified` — an unverified worker is targeted by nothing.
3. From a second account (the web client at https://app.toolbelt.biz is the
   easiest way), post a job in the same trade, inside that worker's radius.
4. The phone should show "New cleaning job nearby".

If nothing arrives, check in this order — these are exactly the filters
`notify_job_posted` applies, and any one of them silently drops the send:

- the token row exists for that user
- `WorkerProfile.vetting_status == VERIFIED`
- `WorkerProfile.is_available` is true
- the job's `trade` equals the profile's `trade`
- the job is within that worker's `service_radius_km` (not the platform default)
- the poster is not the worker (`User.id != job.customer_id`)

Then check the API logs on Render. `ExpoPushSender` logs every rejection Expo
reports, with the error code: `DeviceNotRegistered` means a stale token,
`MismatchSenderId` means the FCM pair from step 2 disagree.

## Before the first *store* release — the bundle identifier

`app.json` sets both platforms to `com.toolbelt.mobile`. Internal builds can
change it freely; after a store listing exists it is effectively permanent, and
changing it means a new listing that existing users must reinstall.

## iOS / TestFlight (needs the $99 account)

```bash
npx eas-cli build --profile production --platform ios
npx eas-cli submit --platform ios
```

EAS handles the APNs key and provisioning profiles itself once your Apple
Developer credentials are attached — iOS needs no Firebase. Expect the first
Apple review of a TestFlight build to take a day or two.
