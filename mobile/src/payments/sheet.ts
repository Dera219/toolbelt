/**
 * Web build of the payment sheet — deliberately unavailable.
 *
 * `@stripe/stripe-react-native` is native-only, and Metro resolves imports
 * statically, so even a dynamic import of it breaks the web bundle. Metro picks
 * `sheet.native.ts` on iOS/Android and this file on web; callers fall back to
 * Stripe's hosted card page there.
 */
export async function collectViaSheet(): Promise<boolean> {
  throw new Error("The payment sheet is only available in the mobile app");
}

export const SHEET_AVAILABLE = false;
