import { Platform } from "react-native";

// iOS simulator reaches the host on localhost; Android emulator uses 10.0.2.2.
// On a physical device, replace with your machine's LAN IP (e.g. http://192.168.1.20:8000).
export const API_URL =
  Platform.OS === "android" ? "http://10.0.2.2:8000" : "http://127.0.0.1:8000";

export const TRADES = [
  "cleaning",
  "moving",
  "handyman",
  "assembly",
  "yard_work",
  "painting",
] as const;
