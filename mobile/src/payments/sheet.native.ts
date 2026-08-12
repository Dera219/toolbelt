import {
  PaymentSheetError,
  initPaymentSheet,
  initStripe,
  presentPaymentSheet,
} from "@stripe/stripe-react-native";
import { api } from "../api/client";

export const SHEET_AVAILABLE = true;

/**
 * Stripe's native PaymentSheet. Card details are entered inside Stripe's own UI
 * and go straight to Stripe — this app never sees a card number.
 *
 * Returns false when the user dismisses the sheet, which is not an error.
 */
export async function collectViaSheet(): Promise<string | null> {
  const setup = await api.startCardSetup();
  if (!setup.publishable_key) {
    throw new Error("Card payments are not configured on the server yet");
  }

  await initStripe({ publishableKey: setup.publishable_key });
  const { error: initError } = await initPaymentSheet({
    merchantDisplayName: "ToolBelt",
    customerId: setup.customer_ref,
    customerEphemeralKeySecret: setup.customer_ephemeral_key_secret,
    setupIntentClientSecret: setup.setup_intent_client_secret,
    allowsDelayedPaymentMethods: false,
    returnURL: "toolbelt://stripe-redirect",
  });
  if (initError) throw new Error(initError.message);

  const { error } = await presentPaymentSheet();
  if (error) {
    if (error.code === PaymentSheetError.Canceled) return null;
    throw new Error(error.message);
  }
  return setup.setup_ref;
}
