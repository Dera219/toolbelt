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
 * Ask for permission, fetch the Expo push token, and register it with the API.
 * Returns null when push is unavailable — a simulator, a denied prompt, or a
 * missing project id. Never throws: no notification is worth blocking login.
 */
export async function registerForPush(): Promise<string | null> {
  try {
    if (!Device.isDevice) {
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
