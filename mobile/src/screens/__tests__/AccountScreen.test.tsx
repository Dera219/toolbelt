/**
 * Screen tests for the account screen.
 *
 * This is where a worker turns on getting paid and a customer saves a card, so
 * the states here are the ones that decide whether money can move at all. Two
 * things make it worth testing carefully.
 *
 * **Everything loads independently and is allowed to fail.** Billing, payouts
 * and balance each `.catch(() => null)`, because a customer legitimately has no
 * payout account and a brand-new worker has no billing profile. That is correct,
 * and it means a real outage looks exactly like a normal empty state — so the
 * tests pin which state produces which button rather than trusting the label.
 *
 * **The web card flow never returns.** On web, `collectCard` navigates the whole
 * page to Stripe and the result is picked up by `resumeCardSetup` on the next
 * mount. A test that only covers the native path would miss half the feature,
 * and the half it misses is the one that runs on the deployed web client.
 *
 * Harness facts these rely on are documented in JobDetailScreen.test.tsx.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import React from "react";

import { ApiError, api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { collectCard, resumeCardSetup } from "../../payments/cardSetup";
import AccountScreen from "../AccountScreen";
import { renderScreen } from "./renderScreen";

jest.mock("../../api/client", () => ({
  ...jest.requireActual("../../api/client"),
  api: {
    getBillingProfile: jest.fn(),
    getPayoutAccount: jest.fn(),
    balance: jest.fn(),
    createPayoutAccount: jest.fn(),
  },
}));

jest.mock("../../payments/cardSetup", () => ({
  collectCard: jest.fn(),
  resumeCardSetup: jest.fn(),
}));

jest.mock("../../auth/AuthContext", () => ({ useAuth: jest.fn() }));

const mockOpenAuthSession = jest.fn();
jest.mock("expo-web-browser", () => ({
  openAuthSessionAsync: (...args: unknown[]) => mockOpenAuthSession(...args),
}));

jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: jest.fn() }),
  useFocusEffect: (callback: () => void) => {
    const React = require("react");
    React.useEffect(callback, [callback]);
  },
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>;
const mockCollectCard = collectCard as jest.MockedFunction<typeof collectCard>;
const mockResume = resumeCardSetup as jest.MockedFunction<typeof resumeCardSetup>;

function asUser(overrides: Record<string, unknown> = {}) {
  mockUseAuth.mockReturnValue({
    user: {
      id: 2,
      email: "worker@example.com",
      full_name: "A Worker",
      role: "worker",
      phone_verified: true,
      ...overrides,
    },
    logout: jest.fn(),
    refreshMe: jest.fn(),
    mode: "worker",
    // The payout section is gated on canWork, which the context derives from
    // role. Omitting it hides the entire "getting paid" half of the screen.
    canWork: true,
    setMode: jest.fn(),
  } as never);
}

/** Nothing configured yet — the state a brand-new account is actually in. */
function nothingConfigured() {
  mockApi.getBillingProfile.mockRejectedValue(new ApiError(404, "no billing"));
  mockApi.getPayoutAccount.mockRejectedValue(new ApiError(404, "no payout account"));
  mockApi.balance.mockRejectedValue(new ApiError(404, "no balance"));
}

beforeEach(() => {
  jest.clearAllMocks();
  asUser();
  nothingConfigured();
  mockResume.mockResolvedValue(null as never);
  mockCollectCard.mockResolvedValue(true as never);
});

describe("the card", () => {
  it("invites a first card when none is saved", async () => {
    await renderScreen(<AccountScreen />);

    expect(await screen.findByText(/Add card/)).toBeTruthy();
    expect(screen.queryByText(/Replace card/)).toBeNull();
  });

  it("offers to replace once a card is on file", async () => {
    mockApi.getBillingProfile.mockResolvedValue({ default_payment_method_ref: "pm_1" } as never);

    await renderScreen(<AccountScreen />);

    expect(await screen.findByText(/Replace card/)).toBeTruthy();
    expect(screen.queryByText(/Add card/)).toBeNull();
  });

  it("re-reads the billing profile after a card is saved", async () => {
    await renderScreen(<AccountScreen />);
    mockApi.getBillingProfile.mockResolvedValue({ default_payment_method_ref: "pm_9" } as never);

    await fireEvent.press(await screen.findByText(/Add card/));

    // The saved card has to be reflected without a manual refresh, or the user
    // taps Add card again and is charged through a second setup flow.
    expect(await screen.findByText("Card saved")).toBeTruthy();
    expect(await screen.findByText(/Replace card/)).toBeTruthy();
  });

  it("says nothing when the web flow navigates away instead of returning", async () => {
    // On web `collectCard` redirects the page to Stripe and never resolves true.
    // Announcing "Card saved" there would be a lie told just before the page
    // disappears.
    mockCollectCard.mockResolvedValue(false as never);

    await renderScreen(<AccountScreen />);
    await fireEvent.press(await screen.findByText(/Add card/));

    expect(screen.queryByText("Card saved")).toBeNull();
  });

  it("reports the outcome Stripe redirected back with", async () => {
    mockResume.mockResolvedValue("Card saved" as never);
    mockApi.getBillingProfile.mockResolvedValue({ default_payment_method_ref: "pm_2" } as never);

    await renderScreen(<AccountScreen />);

    // Without this the web user returns from Stripe to a screen that shows no
    // sign anything happened.
    expect(await screen.findByText("Card saved")).toBeTruthy();
  });
});

describe("getting paid", () => {
  it("offers setup to a worker with no payout account", async () => {
    await renderScreen(<AccountScreen />);

    expect(await screen.findByText(/Set up payouts/)).toBeTruthy();
  });

  it("offers to finish when an account exists but is not enabled", async () => {
    mockApi.getPayoutAccount.mockResolvedValue({
      payouts_enabled: false,
      onboarding_url: "https://connect.stripe.com/x",
    } as never);

    await renderScreen(<AccountScreen />);

    expect(await screen.findByText(/Finish onboarding/)).toBeTruthy();
  });

  it("opens Stripe's hosted onboarding rather than pretending to collect details", async () => {
    mockApi.createPayoutAccount.mockResolvedValue({
      payouts_enabled: false,
      onboarding_url: "https://connect.stripe.com/setup/abc",
    } as never);
    mockOpenAuthSession.mockResolvedValue({ type: "dismiss" });
    // The FIRST call is the initial load and must still find no account, or the
    // button reads "Finish onboarding" and the flow under test never starts.
    // Only the post-onboarding re-read succeeds.
    mockApi.getPayoutAccount
      .mockRejectedValueOnce(new ApiError(404, "no payout account"))
      .mockResolvedValue({ payouts_enabled: true } as never);

    await renderScreen(<AccountScreen />);
    await fireEvent.press(await screen.findByText(/Set up payouts/));

    // Identity and bank details are collected by Stripe. The only way to finish
    // is to actually open the link.
    await waitFor(() =>
      expect(mockOpenAuthSession).toHaveBeenCalledWith(
        "https://connect.stripe.com/setup/abc",
        "toolbelt://payouts"
      )
    );
  });

  it("re-reads the account after onboarding rather than trusting the browser result", async () => {
    mockApi.createPayoutAccount.mockResolvedValue({
      payouts_enabled: false,
      onboarding_url: "https://connect.stripe.com/setup/abc",
    } as never);
    mockOpenAuthSession.mockResolvedValue({ type: "dismiss" });
    // The FIRST call is the initial load and must still find no account, or the
    // button reads "Finish onboarding" and the flow under test never starts.
    // Only the post-onboarding re-read succeeds.
    mockApi.getPayoutAccount
      .mockRejectedValueOnce(new ApiError(404, "no payout account"))
      .mockResolvedValue({ payouts_enabled: true } as never);

    await renderScreen(<AccountScreen />);
    await fireEvent.press(await screen.findByText(/Set up payouts/));

    // Stripe redirects to the server's https return_url, not the app scheme, so
    // the dismissal type says nothing about whether onboarding succeeded. The
    // GET polls the provider, which is the only trustworthy answer.
    expect(await screen.findByText("Payouts are active")).toBeTruthy();
  });

  it("says onboarding is unfinished when the provider still says so", async () => {
    mockApi.createPayoutAccount.mockResolvedValue({
      payouts_enabled: false,
      onboarding_url: "https://connect.stripe.com/setup/abc",
    } as never);
    mockOpenAuthSession.mockResolvedValue({ type: "dismiss" });
    // The FIRST call is the initial load and must still find no account, or the
    // button reads "Finish onboarding" and the flow under test never starts.
    // Only the post-onboarding re-read succeeds.
    mockApi.getPayoutAccount
      .mockRejectedValueOnce(new ApiError(404, "no payout account"))
      .mockResolvedValue({ payouts_enabled: false } as never);

    await renderScreen(<AccountScreen />);
    await fireEvent.press(await screen.findByText(/Set up payouts/));

    expect(await screen.findByText("Onboarding not finished yet — reopen to continue")).toBeTruthy();
  });

  it("does not open a browser when no onboarding link came back", async () => {
    mockApi.createPayoutAccount.mockResolvedValue({
      payouts_enabled: false,
      onboarding_url: null,
    } as never);

    await renderScreen(<AccountScreen />);
    await fireEvent.press(await screen.findByText(/Set up payouts/));

    expect(
      await screen.findByText("Payout account created — onboarding link unavailable, try again")
    ).toBeTruthy();
    expect(mockOpenAuthSession).not.toHaveBeenCalled();
  });

  it("skips onboarding entirely when payouts are already live", async () => {
    mockApi.createPayoutAccount.mockResolvedValue({ payouts_enabled: true } as never);

    await renderScreen(<AccountScreen />);
    await fireEvent.press(await screen.findByText(/Set up payouts/));

    expect(await screen.findByText("Payouts are active")).toBeTruthy();
    expect(mockOpenAuthSession).not.toHaveBeenCalled();
  });
});

describe("when the account endpoints are simply unavailable", () => {
  it("still renders the account rather than blanking", async () => {
    // Each load catches independently and on purpose: a customer has no payout
    // account and a new worker has no billing profile, so an absent one is a
    // normal state rather than an error.
    await renderScreen(<AccountScreen />);

    expect(await screen.findByText(/Add card/)).toBeTruthy();
    expect(screen.getByText(/Log out/)).toBeTruthy();
  });
});
