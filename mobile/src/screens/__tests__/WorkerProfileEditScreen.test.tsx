/**
 * Screen tests for the worker profile editor.
 *
 * This screen sets the numbers the rest of the marketplace runs on. The service
 * radius decides which jobs a worker is notified about *and* which they can see
 * — the two used to disagree, which is the bug NearbyJobsScreen.test.tsx exists
 * for — and the base location decides where "near" is measured from.
 *
 * The subtler thing worth defending is the saved-location fallback. A worker who
 * opens this screen to change their hourly rate should not be blocked by a
 * location prompt, so `resolveCoords` is given the existing base as a last
 * resort. Losing that turns an unrelated edit into a dead end.
 *
 * Harness facts these rely on are documented in JobDetailScreen.test.tsx.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import React from "react";

import { ApiError, api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { resolveCoords } from "../../location";
import WorkerProfileEditScreen from "../worker/WorkerProfileEditScreen";
import { renderScreen } from "./renderScreen";

jest.mock("../../api/client", () => ({
  ...jest.requireActual("../../api/client"),
  api: {
    getWorkerProfile: jest.fn(),
    saveWorkerProfile: jest.fn(),
    requestPhoneCode: jest.fn(),
    verifyPhone: jest.fn(),
    submitVetting: jest.fn(),
  },
}));

jest.mock("../../location", () => ({ resolveCoords: jest.fn() }));
jest.mock("../../auth/AuthContext", () => ({ useAuth: jest.fn() }));
jest.mock("@react-navigation/native", () => ({
  useFocusEffect: (callback: () => void) => {
    const React = require("react");
    React.useEffect(callback, [callback]);
  },
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>;
const mockResolveCoords = resolveCoords as jest.MockedFunction<typeof resolveCoords>;

const UMD = { lat: 38.9869, lng: -76.9426 };

function aProfile(overrides: Record<string, unknown> = {}) {
  return {
    user_id: 2,
    trade: "cleaning",
    bio: "",
    hourly_rate_cents: 5000,
    base_lat: UMD.lat,
    base_lng: UMD.lng,
    service_radius_km: 25,
    is_available: true,
    has_own_tools: false,
    has_vehicle: false,
    vetting_status: "unverified",
    ...overrides,
  };
}

function asUser(overrides: Record<string, unknown> = {}) {
  mockUseAuth.mockReturnValue({
    user: { id: 2, phone_verified: true, ...overrides },
    refreshMe: jest.fn(),
  } as never);
}


beforeEach(() => {
  jest.clearAllMocks();
  asUser();
  mockApi.getWorkerProfile.mockResolvedValue(aProfile() as never);
  mockApi.saveWorkerProfile.mockImplementation(async (body) => ({ ...aProfile(), ...body }) as never);
  mockResolveCoords.mockResolvedValue({ ...UMD, source: "gps" });
});

describe("the service radius", () => {
  it("saves the radius the worker typed", async () => {
    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Service radius (km)");

    await fireEvent.changeText(screen.getByDisplayValue("25"), "60");
    await fireEvent.press(screen.getByText(/Save profile/));

    await waitFor(() => expect(mockApi.saveWorkerProfile).toHaveBeenCalled());
    // This number decides both which jobs are pushed to the worker and which
    // they can see. The two disagreeing is the bug the nearby screen covers.
    expect(mockApi.saveWorkerProfile.mock.calls[0][0].service_radius_km).toBe(60);
  });

  it.each([
    ["zero", "0"],
    ["negative", "-10"],
    ["beyond the platform cap", "500"],
    ["not a number", "wide"],
  ])("refuses a %s radius without calling the API", async (_label, value) => {
    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Service radius (km)");

    await fireEvent.changeText(screen.getByDisplayValue("25"), value);
    await fireEvent.press(screen.getByText(/Save profile/));

    // The bound matches the API schema (le=100) and max_search_radius_km. A
    // client that accepted more would produce a 422 the worker cannot act on.
    expect(await screen.findByText("Service radius must be 1–100 km")).toBeTruthy();
    expect(mockApi.saveWorkerProfile).not.toHaveBeenCalled();
  });
});

describe("the hourly rate", () => {
  it("converts the typed rate to cents", async () => {
    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Hourly rate");

    await fireEvent.changeText(screen.getByDisplayValue("50.00"), "62.50");
    await fireEvent.press(screen.getByText(/Save profile/));

    await waitFor(() => expect(mockApi.saveWorkerProfile).toHaveBeenCalled());
    expect(mockApi.saveWorkerProfile.mock.calls[0][0].hourly_rate_cents).toBe(6250);
  });

  it("refuses a rate that is not a positive amount", async () => {
    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Hourly rate");

    await fireEvent.changeText(screen.getByDisplayValue("50.00"), "0");
    await fireEvent.press(screen.getByText(/Save profile/));

    expect(await screen.findByText("Hourly rate must be a positive amount")).toBeTruthy();
    expect(mockApi.saveWorkerProfile).not.toHaveBeenCalled();
  });
});

describe("where the base location comes from", () => {
  it("offers the existing base as a fallback, so an unrelated edit is not blocked", async () => {
    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Service radius (km)");
    await fireEvent.press(screen.getByText(/Save profile/));

    // Without `saved`, a worker with location off who only wanted to change
    // their rate would be stopped by a permission prompt.
    await waitFor(() =>
      expect(mockResolveCoords).toHaveBeenCalledWith(
        expect.objectContaining({ saved: { lat: UMD.lat, lng: UMD.lng } })
      )
    );
  });

  it("says when the existing base was kept rather than silently reusing it", async () => {
    mockResolveCoords.mockResolvedValue({ ...UMD, source: "saved" });

    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Service radius (km)");
    await fireEvent.press(screen.getByText(/Save profile/));

    // A worker who thinks they just moved their base needs to know they did not.
    expect(
      await screen.findByText("Profile saved — your existing base location was kept")
    ).toBeTruthy();
  });

  it("says when the base came from the typed address", async () => {
    mockResolveCoords.mockResolvedValue({ ...UMD, source: "address" });

    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Service radius (km)");
    await fireEvent.press(screen.getByText(/Save profile/));

    expect(
      await screen.findByText(
        "Profile saved — base location set from the address you entered"
      )
    ).toBeTruthy();
  });

  it("does not save a profile when no location could be resolved at all", async () => {
    mockResolveCoords.mockRejectedValue(new ApiError(0, "Allow location access, or type the town"));

    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Service radius (km)");
    await fireEvent.press(screen.getByText(/Save profile/));

    // A worker profile with no base is unreachable by the matcher, exactly like
    // a job with no coordinates.
    expect(await screen.findByText(/Allow location access/)).toBeTruthy();
    expect(mockApi.saveWorkerProfile).not.toHaveBeenCalled();
  });
});

describe("the vetting gate", () => {
  it("offers vetting to a phone-verified worker who has not been vetted", async () => {
    await renderScreen(<WorkerProfileEditScreen />);

    expect(await screen.findByText("Submit for vetting")).toBeTruthy();
  });

  it("does not offer vetting before the phone is verified", async () => {
    asUser({ phone_verified: false });

    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Service radius (km)");

    // The server refuses vetting without a verified phone, so offering it here
    // would produce a 409 the worker can do nothing about.
    expect(screen.queryByText("Submit for vetting")).toBeNull();
  });

  it("does not offer vetting again once it is pending", async () => {
    mockApi.getWorkerProfile.mockResolvedValue(aProfile({ vetting_status: "pending" }) as never);

    await renderScreen(<WorkerProfileEditScreen />);
    await screen.findByText("Service radius (km)");

    expect(screen.queryByText("Submit for vetting")).toBeNull();
  });

  it("offers vetting again after a rejection", async () => {
    mockApi.getWorkerProfile.mockResolvedValue(aProfile({ vetting_status: "rejected" }) as never);

    await renderScreen(<WorkerProfileEditScreen />);

    // A rejection is not permanent; the worker fixes what was wrong and resubmits.
    expect(await screen.findByText("Submit for vetting")).toBeTruthy();
  });
});
