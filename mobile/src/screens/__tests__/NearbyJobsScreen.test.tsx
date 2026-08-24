/**
 * Screen tests for the worker's nearby-jobs list.
 *
 * The suite already covers this screen's *logic* — that `nearbyJobs` puts
 * `radius_km` in the query string lives in `api/__tests__/client.test.ts`. What
 * nothing covered until now is the screen itself: which of its four states a
 * worker actually lands in, and whether the values it passes down are the ones
 * it claims to.
 *
 * That distinction is not academic. The `radius_km` bug was a screen failing to
 * pass a value it had in hand, and a passing client test would not have caught
 * it, because the client was only ever called with what the screen chose to
 * send. The test below asserts the screen supplies the worker's own radius, not
 * merely that the client would forward one.
 */

import { screen, waitFor } from "@testing-library/react-native";

import { renderScreen } from "./renderScreen";
import React from "react";

import { ApiError, api } from "../../api/client";
import { resolveCoords } from "../../location";
import NearbyJobsScreen from "../worker/NearbyJobsScreen";

// Keep the real module and replace only the network calls. A wholesale mock
// drops helpers the rendered tree needs (`money`, used by JobCard) and, worse,
// substitutes a fake ApiError — so the screen's `instanceof ApiError` branch
// would be exercised against a class production never sees.
jest.mock("../../api/client", () => ({
  ...jest.requireActual("../../api/client"),
  api: {
    getWorkerProfile: jest.fn(),
    nearbyJobs: jest.fn(),
  },
}));

jest.mock("../../location", () => ({ resolveCoords: jest.fn() }));

const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
  // Run the effect body once, immediately, the way a focused screen would.
  useFocusEffect: (callback: () => void) => {
    const React = require("react");
    React.useEffect(callback, [callback]);
  },
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockResolveCoords = resolveCoords as jest.MockedFunction<typeof resolveCoords>;

const UMD = { lat: 38.9869, lng: -76.9426 };

function workerProfile(overrides: Record<string, unknown> = {}) {
  return {
    user_id: 1,
    trade: "cleaning",
    bio: "",
    hourly_rate_cents: 5000,
    base_lat: UMD.lat,
    base_lng: UMD.lng,
    service_radius_km: 40,
    is_available: true,
    vetting_status: "verified",
    ...overrides,
  };
}

function job(id: number, title: string) {
  return {
    id,
    title,
    trade: "cleaning",
    status: "open",
    distance_km: 2.5,
    budget_cents: 8000,
    currency: "USD",
    description: "",
    lat: UMD.lat,
    lng: UMD.lng,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockResolveCoords.mockResolvedValue({ ...UMD, source: "gps" });
  mockApi.getWorkerProfile.mockResolvedValue(workerProfile() as never);
  mockApi.nearbyJobs.mockResolvedValue([] as never);
});

describe("what the screen sends to the API", () => {
  it("passes the worker's own service radius, not the platform default", async () => {
    mockApi.getWorkerProfile.mockResolvedValue(
      workerProfile({ service_radius_km: 60, trade: "moving" }) as never
    );

    await renderScreen(<NearbyJobsScreen />);

    // The regression this screen shipped with: the radius was in hand and was
    // never passed, so the server silently applied its own 25km default and
    // "Widen my radius" changed nothing.
    await waitFor(() =>
      expect(mockApi.nearbyJobs).toHaveBeenCalledWith(UMD.lat, UMD.lng, "moving", 60)
    );
  });

  it("searches around the resolved position rather than the saved base", async () => {
    // A worker who has travelled: GPS says Baltimore, the profile still says UMD.
    mockResolveCoords.mockResolvedValue({ lat: 39.29, lng: -76.61, source: "gps" });

    await renderScreen(<NearbyJobsScreen />);

    await waitFor(() =>
      expect(mockApi.nearbyJobs).toHaveBeenCalledWith(39.29, -76.61, "cleaning", 40)
    );
  });
});

describe("which state the worker lands in", () => {
  it("offers profile setup when there is no worker profile", async () => {
    mockApi.getWorkerProfile.mockRejectedValue(new ApiError(404, "No worker profile"));

    await renderScreen(<NearbyJobsScreen />);

    expect(await screen.findByText("Set up your worker profile")).toBeTruthy();
    // A 404 here is a state, not a failure. Showing an error would tell a new
    // worker something is broken when nothing is.
    expect(screen.queryByText(/could not load/i)).toBeNull();
  });

  it("lets an unverified worker browse, while saying offers are gated", async () => {
    mockApi.getWorkerProfile.mockResolvedValue(
      workerProfile({ vetting_status: "pending" }) as never
    );
    mockApi.nearbyJobs.mockResolvedValue([job(1, "Deep clean, 2 bed")] as never);

    await renderScreen(<NearbyJobsScreen />);

    expect(await screen.findByText("Vetting")).toBeTruthy();
    // Browsing must still work — hiding the jobs would make vetting look like a
    // wall rather than a step.
    expect(screen.getByText("Deep clean, 2 bed")).toBeTruthy();
  });

  it("shows nothing about vetting once verified", async () => {
    mockApi.nearbyJobs.mockResolvedValue([job(1, "Deep clean, 2 bed")] as never);

    await renderScreen(<NearbyJobsScreen />);

    await screen.findByText("Deep clean, 2 bed");
    expect(screen.queryByText("Vetting")).toBeNull();
  });

  it("offers a way out of an empty list instead of a dead end", async () => {
    await renderScreen(<NearbyJobsScreen />);

    expect(await screen.findByText("No open jobs nearby")).toBeTruthy();
    expect(screen.getByText("Widen my radius")).toBeTruthy();
  });

  it("surfaces a real failure rather than an empty list", async () => {
    mockApi.nearbyJobs.mockRejectedValue(new ApiError(500, "Could not load jobs"));

    await renderScreen(<NearbyJobsScreen />);

    // An outage that renders as "no jobs nearby" is worse than an error: the
    // worker concludes there is no work and stops opening the app.
    expect(await screen.findByText("Could not load jobs")).toBeTruthy();
    expect(screen.queryByText("No open jobs nearby")).toBeNull();
  });

  it("says so when the location came from the saved base rather than GPS", async () => {
    mockResolveCoords.mockResolvedValue({ ...UMD, source: "saved" });

    await renderScreen(<NearbyJobsScreen />);

    // Distances are measured from somewhere the worker may not be. Saying which
    // is the difference between a wrong list and an explained one.
    expect(
      await screen.findByText("Showing jobs around your saved base location.")
    ).toBeTruthy();
  });
});
