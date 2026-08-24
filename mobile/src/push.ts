/**
 * Push notification registration.
 *
 * The API decides who to notify; this file only obtains a device token, hands
 * it to the API, and surrenders it on logout. Registration is idempotent, so
 * calling it on every launch is correct and cheap.
 */

import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { api } from "./api/client";

// Show a banner even when the app is foregrounded — a worker looking at the
// job list still needs to see that a new job just landed.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

let cachedToken: string | null = null;

/** The token last registered, so logout can unregister exactly that device. */
export function currentPushToken(): string | null {
  return cachedToken;
}

/**
 * Deliberate escape hatch for verifying push without owning an Android phone.
 *
 * `Device.isDevice` is false on an emulator, and the guard below uses it because
 * an iOS Simulator genuinely cannot receive a remote notification. An **Android**
 * emulator running a Google Play services image can: it registers with FCM and
 * receives normally. So this guard, and nothing else, is what stands between a
 * developer with no test handset and an end-to-end verification of the whole
 * push chain.
 *
 * Two locks, because a production build that registers emulator tokens would be
 * a real defect: `__DEV__` is false in any release build, and the variable must
 * be set on purpose. Neither alone would be enough.
 *
 *     EXPO_PUBLIC_ALLOW_EMULATOR_PUSH=1 npx expo start
 */
function emulatorPushAllowed(): boolean {
  return __DEV__ && process.env.EXPO_PUBLIC_ALLOW_EMULATOR_PUSH === "1";
}

/**
 * Ask for permission, fetch the Expo push token, and register it with the API.
 * Returns null when push is unavailable — a simulator, a denied prompt, or a
 * missing project id. Never throws: no notification is worth blocking login.
 */
export async function registerForPush(): Promise<string | null> {
  try {
    if (!Device.isDevice && !emulatorPushAllowed()) {
      // Simulators cannot receive push. Not an error, just nothing to do.
      return null;
    }

    const existing = await Notifications.getPermissionsAsync();
    let status = existing.status;
    if (status !== "granted") {
      const asked = await Notifications.requestPermissionsAsync();
      status = asked.status;
    }
    if (status !== "granted") return null;

    if (Platform.OS === "android") {
      await Notifications.setNotificationChannelAsync("default", {
        name: "Job alerts",
        importance: Notifications.AndroidImportance.HIGH,
      });
    }

    const projectId =
      Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
    const { data: token } = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined,
    );

    await api.registerDeviceToken(token, Platform.OS);
    cachedToken = token;
    return token;
  } catch (err) {
    // Push is a convenience. Log and move on.
    console.warn("Push registration skipped:", err);
    return null;
  }
}

/** Release this device so the next person to log in here does not get your jobs. */
export async function unregisterPush(): Promise<void> {
  if (!cachedToken) return;
  try {
    await api.unregisterDeviceToken(cachedToken);
  } catch {
    // Best effort — the server also re-points a token when someone else registers it.
  } finally {
    cachedToken = null;
  }
}

/**
 * Wire notification taps to navigation.
 *
 * `data.type` and `data.job_id` are set by the API in
 * app/modules/notifications/service.py. Returns an unsubscribe function.
 */
export function onNotificationTap(
  handler: (data: { type?: string; job_id?: number }) => void,
): () => void {
  const sub = Notifications.addNotificationResponseReceivedListener((response) => {
    const data = response.notification.request.content.data as {
      type?: string;
      job_id?: number;
    };
    handler(data ?? {});
  });
  return () => sub.remove();
}
