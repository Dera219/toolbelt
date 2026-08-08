import Constants from "expo-constants";
import { Platform } from "react-native";

const API_PORT = 8000;

/**
 * Resolve the API base URL.
 *
 * On a physical device, `localhost` points at the phone itself, so we reuse the
 * dev-server host Expo already told the app about (e.g. "10.172.91.114:8081")
 * and swap in the API port. Falls back to emulator-friendly loopback addresses.
 * Set EXPO_PUBLIC_API_URL to override (e.g. a staging deploy).
 */
function resolveApiUrl(): string {
  const override = process.env.EXPO_PUBLIC_API_URL;
  if (override) return override;

  const hostUri = Constants.expoConfig?.hostUri ?? Constants.expoGoConfig?.debuggerHost;
  const host = hostUri?.split(":")[0];
  if (host && host !== "localhost" && host !== "127.0.0.1") {
    return `http://${host}:${API_PORT}`;
  }
  // Android emulator reaches the host machine on 10.0.2.2; iOS simulator on localhost.
  return Platform.OS === "android"
    ? `http://10.0.2.2:${API_PORT}`
    : `http://127.0.0.1:${API_PORT}`;
}

export const API_URL = resolveApiUrl();

export const TRADES = [
  "cleaning",
  "moving",
  "handyman",
  "assembly",
  "yard_work",
  "painting",
] as const;
