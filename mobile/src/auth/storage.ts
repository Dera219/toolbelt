import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

/**
 * Token storage. Native uses the device keychain (expo-secure-store); the web
 * build falls back to localStorage, which SecureStore does not support.
 * Web is a development convenience only — never a shipping target for auth.
 */
export const tokenStore = {
  async get(key: string): Promise<string | null> {
    if (Platform.OS === "web") {
      try {
        return globalThis.localStorage?.getItem(key) ?? null;
      } catch {
        return null;
      }
    }
    return SecureStore.getItemAsync(key);
  },

  async set(key: string, value: string): Promise<void> {
    if (Platform.OS === "web") {
      try {
        globalThis.localStorage?.setItem(key, value);
      } catch {
        // storage disabled (private mode) — session stays in memory only
      }
      return;
    }
    await SecureStore.setItemAsync(key, value);
  },

  async remove(key: string): Promise<void> {
    if (Platform.OS === "web") {
      try {
        globalThis.localStorage?.removeItem(key);
      } catch {
        // nothing to clear
      }
      return;
    }
    await SecureStore.deleteItemAsync(key);
  },
};
