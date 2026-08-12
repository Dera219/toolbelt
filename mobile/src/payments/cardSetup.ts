import * as WebBrowser from "expo-web-browser";
import { Platform } from "react-native";
import { api } from "../api/client";
import { API_URL } from "../config";

/**
 * Collect and save a card.
 *
 * Native uses Stripe's PaymentSheet. The web build cannot — @stripe/stripe-react-native
 * has no browser implementation — so it falls back to Stripe's hosted card page.
 * Either way the card goes from the user's device straight to Stripe, and this
 * app only ever learns that *a* card was saved.
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

async function collectViaSheet(): Promise<boolean> {
  // Imported lazily: the module pulls in native code that does not exist on web,
  // and a top-level import would break the web bundle even behind a check.
  const stripe = await import("@stripe/stripe-react-native");
  const setup = await api.startCardSetup();
  if (!setup.publishable_key) {
    throw new Error("Card payments are not configured on the server yet");
  }

  await stripe.initStripe({ publishableKey: setup.publishable_key });
  const { error: initError } = await stripe.initPaymentSheet({
    merchantDisplayName: "ToolBelt",
    customerId: setup.customer_ref,
    customerEphemeralKeySecret: setup.customer_ephemeral_key_secret,
    setupIntentClientSecret: setup.setup_intent_client_secret,
    allowsDelayedPaymentMethods: false,
    returnURL: "toolbelt://stripe-redirect",
  });
  if (initError) throw new Error(initError.message);

  const { error } = await stripe.presentPaymentSheet();
  if (error) {
    if (error.code === stripe.PaymentSheetError.Canceled) return false;
    throw new Error(error.message);
  }
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
