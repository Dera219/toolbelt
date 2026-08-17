/**
 * Tests for the payment sheet's platform split.
 *
 * `cardSetup.ts` imports `./sheet` and the bundler decides which file that is:
 * `sheet.native.ts` on iOS and Android, `sheet.ts` everywhere else. The web
 * variant exists solely to keep `@stripe/stripe-react-native` out of the web
 * bundle — the SDK is native-only, and Metro resolves imports statically, so
 * even an unreachable import of it breaks the build. app.toolbelt.biz is a
 * shipped product, so that resolution is load-bearing.
 *
 * Nothing else in the suite would catch a regression here. A single test
 * running under one platform proves nothing about the other, which is the whole
 * reason the suite runs as per-platform projects.
 */

import { Platform } from "react-native";

import { SHEET_AVAILABLE, collectViaSheet } from "../sheet";

// The SDK is a native binary module: importing it under Jest throws
// "TurboModuleRegistry.getEnforcing('StripeSdk') could not be found". Mocking
// it lets the native variant load far enough to be inspected. On web this mock
// is simply never consulted, which is itself the property under test.
jest.mock("@stripe/stripe-react-native", () => ({
  __esModule: true,
  initStripe: jest.fn(),
  initPaymentSheet: jest.fn(),
  presentPaymentSheet: jest.fn(),
  PaymentSheetError: { Canceled: "Canceled" },
}));

const isNative = Platform.OS === "ios" || Platform.OS === "android";

describe(`payment sheet on ${Platform.OS}`, () => {
  it("resolves the variant that belongs to this platform", () => {
    // Written as an equivalence rather than a hardcoded expectation so the
    // assertion is meaningful in all four projects instead of vacuously true
    // in three of them.
    expect(SHEET_AVAILABLE).toBe(isNative);
  });

  it("exposes collectViaSheet either way, so callers need no platform check", () => {
    expect(typeof collectViaSheet).toBe("function");
  });

  if (!isNative) {
    it("refuses on web instead of pretending to open a sheet", async () => {
      // cardSetup.ts redirects to Stripe's hosted page on web and never calls
      // this. If it ever did, failing loudly beats a silent no-op that leaves
      // the user staring at a screen where nothing happened.
      await expect(collectViaSheet()).rejects.toThrow(
        /only available in the mobile app/i
      );
    });
  }
});
