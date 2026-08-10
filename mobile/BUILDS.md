# Builds & push notifications

## The constraint that decides everything here

**Remote push notifications do not work in Expo Go.** Support was removed in
SDK 53, and this project is on SDK 57. Scanning the QR code with Expo Go still
runs the app — but a device registered that way will never receive a job alert,
and `registerForPush()` returns null.

Testing push therefore requires a **development build**, which is a real app
binary containing the native notification module.

| Target | Cost | Gets you |
|---|---|---|
| Expo Go | free | The app, no push. Fine for UI and flow work. |
| Android dev build | **free** | Real push, installable APK, no store account |
| iOS dev build / TestFlight | **$99/yr** Apple Developer | Real push on iPhone, TestFlight distribution |

**Android first is the cheap path.** It costs nothing, tests the entire push
system end to end, and needs no Apple relationship. Do that before spending $99.

## Before the first build — check the bundle identifier

`app.json` sets both to `com.toolbelt.mobile`. **Change it now if you want
something else**: after an app is published, the identifier is effectively
permanent — changing it means a new listing that existing users must reinstall.

If you buy a domain, reverse it: owning `toolbelt.build` would make
`build.toolbelt.mobile` the conventional choice.

## Android development build (free, ~15 minutes)

```bash
npm install -g eas-cli
eas login                  # free Expo account; interactive
eas build:configure        # links the project, writes the EAS project id
eas build --profile development --platform android
```

EAS builds in the cloud and returns an install URL. Open it on an Android phone,
install, then:

```bash
npx expo start --dev-client
```

Now push works: post a job from another account and the device gets an alert.

## iOS / TestFlight (needs the $99 account)

```bash
eas build --profile production --platform ios
eas submit --platform ios
```

EAS handles the push certificate and provisioning profiles itself once your
Apple Developer credentials are attached. Expect the first Apple review of a
TestFlight build to take a day or two.

## Fix the API URLs before a real build

`eas.json` has placeholder hosts in the `preview` and `production` profiles:

```
"EXPO_PUBLIC_API_URL": "https://staging.example.com"
"EXPO_PUBLIC_API_URL": "https://api.example.com"
```

The API is not deployed anywhere yet. A build made now would ship pointing at a
domain that does not exist. The `development` profile points at `localhost`,
which is correct for a simulator but wrong for a physical device — override with
your machine's LAN IP, or use the tunnel described in `src/config.ts`.

**Deploying the API is the real prerequisite for a pilot**, not the build. A
TestFlight app talking to a laptop that sleeps is not a pilot.

## Verifying push actually works

Once a dev build is on a device:

1. Log in — `registerForPush()` runs and posts the token to `/me/device-tokens`
2. Confirm it landed: `sqlite3 toolbelt.db "select user_id, platform from device_tokens;"`
3. From a second account, post a job matching that worker's trade and area
4. The device should show "New cleaning job nearby"

If nothing arrives, check in this order: the token row exists; the worker's
`vetting_status` is `verified`; the job's trade matches the profile's `trade`;
and the job is within the worker's `service_radius_km`. Those four filters are
what `notify_job_posted` applies, and any one of them silently drops the send.
