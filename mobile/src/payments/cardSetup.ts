import * as WebBrowser from "expo-web-browser";
import { Platform } from "react-native";
import { api } from "../api/client";
import { API_URL } from "../config";
import { collectViaSheet } from "./sheet";

/**
 * Collect and save a card.
 *
 * Native uses Stripe's PaymentSheet; the web build uses Stripe's hosted card
 * page, because the native SDK has no browser implementation. Either way the
 * card goes from the user's device straight to Stripe, and this app only ever
 * learns that *a* card was saved.
 *
 * Returns false when the user backs out, which is not an error.
 */
export async function collectCard(): Promise<boolean> {
  const setupRef =
    Platform.OS === "web" ? await collectViaHostedPage() : await collectViaSheet();
  if (setupRef === null) return false;
  // The reference names which setup completed; the server resolves the saved
  // payment method from it directly rather than guessing from a list. The id
  // alone proves nothing — the server still verifies it against the provider.
  await api.confirmCard(setupRef || undefined);
  return true;
}

/** Returns the completed setup's reference, or null if the user backed out. */
async function collectViaHostedPage(): Promise<string | null> {
  const returnUrl = typeof window !== "undefined" ? window.location.origin : API_URL;
  const { url } = await api.startCardSetupSession(returnUrl);

  const result = await WebBrowser.openAuthSessionAsync(url, returnUrl);
  if (result.type !== "success") return null;
  // Stripe appends the outcome and the session id to the return URL.
  if (result.url.includes("card=cancelled")) return null;
  const sessionId = new URL(result.url).searchParams.get("session_id");
  return sessionId ?? "";
}
