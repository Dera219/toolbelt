import { Platform } from "react-native";
import { api } from "../api/client";
import { collectViaSheet } from "./sheet";

/**
 * Collect and save a card.
 *
 * Native opens Stripe's PaymentSheet. The web build cannot — the native SDK has
 * no browser implementation — so it redirects the whole page to Stripe's hosted
 * card page and picks the result back up on return (see `resumeCardSetup`).
 *
 * A popup was tried first and does not work: Cross-Origin-Opener-Policy on
 * Stripe's page severs the opener relationship, so the app can never observe
 * the popup closing. It waits forever, and switching tabs leaves a stuck
 * screen. A full-page redirect has no such dependency and also survives popup
 * blockers.
 *
 * Returns true when a card was saved. On web it does not return at all — the
 * page navigates away.
 */
export async function collectCard(): Promise<boolean> {
  if (Platform.OS === "web") {
    await redirectToHostedPage();
    return false; // unreachable in practice; the page is navigating
  }
  const setupRef = await collectViaSheet();
  if (setupRef === null) return false; // user dismissed the sheet
  await api.confirmCard(setupRef);
  return true;
}

async function redirectToHostedPage(): Promise<void> {
  const returnUrl = window.location.origin;
  const { url } = await api.startCardSetupSession(returnUrl);
  window.location.assign(url);
}

/**
 * Finish a redirect-based card setup.
 *
 * Stripe returns to the app with `?card=saved&session_id=…`. Confirm that
 * session with the server, then strip the parameters so a refresh does not
 * re-run it. Returns a message to show the user, or null if this was an
 * ordinary page load.
 */
export async function resumeCardSetup(): Promise<string | null> {
  if (Platform.OS !== "web" || typeof window === "undefined") return null;

  const params = new URLSearchParams(window.location.search);
  const outcome = params.get("card");
  if (!outcome) return null;

  const sessionId = params.get("session_id");
  // Clear first: whatever happens next, a reload must not repeat this.
  params.delete("card");
  params.delete("session_id");
  const query = params.toString();
  window.history.replaceState({}, "", window.location.pathname + (query ? `?${query}` : ""));

  if (outcome !== "saved") return "Card setup cancelled";
  try {
    await api.confirmCard(sessionId ?? undefined);
    return "Card saved";
  } catch (e) {
    return e instanceof Error ? e.message : "Could not save that card";
  }
}
