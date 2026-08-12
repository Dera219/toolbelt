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
  const saved = Platform.OS === "web" ? await collectViaHostedPage() : await collectViaSheet();
  if (!saved) return false;
  // Ask our server to record what Stripe actually holds — never a client-supplied id.
  await api.confirmCard();
  return true;
}

async function collectViaHostedPage(): Promise<boolean> {
  const returnUrl = typeof window !== "undefined" ? window.location.origin : API_URL;
  const { url } = await api.startCardSetupSession(returnUrl);

  const result = await WebBrowser.openAuthSessionAsync(url, returnUrl);
  if (result.type !== "success") return false;
  // Stripe appends the outcome to the return URL.
  return !result.url.includes("card=cancelled");
}
