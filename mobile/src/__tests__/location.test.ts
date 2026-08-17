/**
 * Tests for coordinate resolution.
 *
 * A lat/lng is mandatory for the whole marketplace: the API matches workers to
 * jobs with a bounding box plus haversine, so a job posted without coordinates
 * is invisible to everyone. That makes the fallback chain — GPS, then a typed
 * address, then a saved location — load-bearing rather than a nicety, and makes
 * the rejection of a bad coordinate more important than accepting a good one.
 *
 * The (0, 0) case deserves the attention it gets below. It is open ocean off
 * West Africa and in practice always means an unset database column, so
 * accepting it does not fail — it quietly shows a worker jobs on another
 * continent, or hides a job from everyone near it.
 */

import * as Location from "expo-location";

import { ApiError } from "../api/client";
import { resolveCoords } from "../location";

jest.mock("expo-location", () => ({
  requestForegroundPermissionsAsync: jest.fn(),
  getCurrentPositionAsync: jest.fn(),
  geocodeAsync: jest.fn(),
}));

const mockLocation = Location as jest.Mocked<typeof Location>;

const UMD = { lat: 38.9869, lng: -76.9426 };
const MESSAGE = "Allow location access, or set your base location.";

/** GPS unavailable: the permission prompt is answered with a refusal. */
function denyGps() {
  mockLocation.requestForegroundPermissionsAsync.mockResolvedValue({
    status: "denied",
  } as never);
}

function grantGps(lat: number, lng: number) {
  mockLocation.requestForegroundPermissionsAsync.mockResolvedValue({
    status: "granted",
  } as never);
  mockLocation.getCurrentPositionAsync.mockResolvedValue({
    coords: { latitude: lat, longitude: lng },
  } as never);
}

/**
 * Make both geocoders answer, because which one runs depends on the platform.
 *
 * `geocodeAddress` only consults the OS geocoder off web; on web it goes
 * straight to the hosted one. A test that mocks just `Location.geocodeAsync` is
 * therefore a native-only test wearing a platform-agnostic name — and under the
 * web project it silently reached the real Photon service over the network.
 */
function geocoderReturns(lat: number, lng: number) {
  mockLocation.geocodeAsync.mockResolvedValue([{ latitude: lat, longitude: lng }] as never);
  global.fetch = jest.fn(async () => ({
    ok: true,
    status: 200,
    // GeoJSON orders coordinates [longitude, latitude].
    json: async () => ({ features: [{ geometry: { coordinates: [lng, lat] } }] }),
  })) as unknown as typeof fetch;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockLocation.geocodeAsync.mockResolvedValue([] as never);
  // Re-arm the network denial after clearAllMocks wipes the one from
  // jest.setup.ts. Without this the suite passes or fails based on whether the
  // machine running it has internet, which is not a test.
  global.fetch = jest.fn(async () => {
    throw new Error("Unmocked fetch in a location test");
  }) as unknown as typeof fetch;
});

describe("source preference", () => {
  it("uses GPS when permission is granted, and asks no geocoder", async () => {
    grantGps(UMD.lat, UMD.lng);

    await expect(
      resolveCoords({ address: "College Park MD", saved: UMD, unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "gps" });

    // A GPS fix must not cost a third-party request on top.
    expect(mockLocation.geocodeAsync).not.toHaveBeenCalled();
  });

  it("falls back to the typed address when permission is denied", async () => {
    denyGps();
    geocoderReturns(UMD.lat, UMD.lng);

    await expect(
      resolveCoords({ address: "College Park MD", unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "address" });
  });

  it("falls back to the saved location when there is no address to geocode", async () => {
    denyGps();

    await expect(
      resolveCoords({ saved: UMD, unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "saved" });
  });

  it("prefers a geocoded address over a stale saved location", async () => {
    denyGps();
    geocoderReturns(39.29, -76.61);

    const result = await resolveCoords({
      address: "Baltimore MD",
      saved: UMD,
      unavailableMessage: MESSAGE,
    });

    // What the user typed on this screen beats what they saved months ago.
    expect(result.source).toBe("address");
  });
});

describe("failure is reported, not thrown from underneath", () => {
  it("raises ApiError status 0 carrying the caller's message when nothing resolves", async () => {
    denyGps();

    // Status 0 is this codebase's marker for a failure with no HTTP response
    // behind it, matching the transport error in api/client.ts.
    await expect(
      resolveCoords({ unavailableMessage: MESSAGE })
    ).rejects.toMatchObject({ status: 0, message: MESSAGE });
  });

  it("survives a permission check that throws instead of resolving", async () => {
    // requestForegroundPermissionsAsync throws on web when navigator.permissions
    // is missing — older browsers, or any page served over plain http.
    mockLocation.requestForegroundPermissionsAsync.mockRejectedValue(
      new Error("navigator.permissions is undefined")
    );

    await expect(
      resolveCoords({ saved: UMD, unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "saved" });
  });

  it("survives an OS geocoder that throws, e.g. Android without Play services", async () => {
    denyGps();
    mockLocation.geocodeAsync.mockRejectedValue(new Error("E_NO_GEOCODER"));
    // The hosted geocoder is denied by the default beforeEach, which is what
    // makes this meaningful on web where geocodeAsync is never reached.

    await expect(
      resolveCoords({ address: "College Park MD", saved: UMD, unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "saved" });
  });
});

describe("coordinate validation", () => {
  it("treats a saved (0, 0) as unset rather than as the Gulf of Guinea", async () => {
    denyGps();

    await expect(
      resolveCoords({ saved: { lat: 0, lng: 0 }, unavailableMessage: MESSAGE })
    ).rejects.toBeInstanceOf(ApiError);
  });

  it.each([
    ["latitude past the pole", { lat: 91, lng: 0 }],
    ["longitude past the antimeridian", { lat: 0, lng: 181 }],
    ["NaN", { lat: Number.NaN, lng: Number.NaN }],
  ])("rejects a saved location with %s", async (_label, saved) => {
    denyGps();

    // These are exactly the values the API's own schemas refuse, so catching
    // them here turns a confusing 422 into an actionable message.
    await expect(
      resolveCoords({ saved, unavailableMessage: MESSAGE })
    ).rejects.toBeInstanceOf(ApiError);
  });

  it("rejects a GPS fix that reports (0, 0) and moves on to the fallback", async () => {
    grantGps(0, 0);

    await expect(
      resolveCoords({ saved: UMD, unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "saved" });
  });
});

describe("hosted geocoder fallback", () => {
  it("reads Photon's GeoJSON coordinates in [lng, lat] order, not [lat, lng]", async () => {
    denyGps();
    mockLocation.geocodeAsync.mockRejectedValue(new Error("E_NO_GEOCODER"));

    // Photon answers in GeoJSON, which orders longitude first. Reading these
    // positionally the obvious way puts College Park in the Indian Ocean, and
    // both numbers stay individually plausible — so nothing downstream errors.
    global.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        features: [{ geometry: { coordinates: [UMD.lng, UMD.lat] } }],
      }),
    })) as unknown as typeof fetch;

    await expect(
      resolveCoords({ address: "College Park MD", unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "address" });
  });

  it("treats a geocoder miss as no answer rather than an error", async () => {
    denyGps();
    mockLocation.geocodeAsync.mockRejectedValue(new Error("E_NO_GEOCODER"));

    global.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ features: [] }),
    })) as unknown as typeof fetch;

    await expect(
      resolveCoords({ address: "asdfghjkl qwerty", saved: UMD, unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "saved" });
  });

  it("does not let a geocoder outage break the saved-location fallback", async () => {
    denyGps();
    mockLocation.geocodeAsync.mockRejectedValue(new Error("E_NO_GEOCODER"));

    global.fetch = jest.fn(async () => {
      throw new TypeError("Network request failed");
    }) as unknown as typeof fetch;

    await expect(
      resolveCoords({ address: "College Park MD", saved: UMD, unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "saved" });
  });
});

describe("geocoder frugality", () => {
  it("does not spend a request on an address too short to be one", async () => {
    denyGps();

    await expect(
      resolveCoords({ address: "hi", unavailableMessage: MESSAGE })
    ).rejects.toBeInstanceOf(ApiError);

    expect(mockLocation.geocodeAsync).not.toHaveBeenCalled();
    // And on web, where geocodeAsync is never consulted at all, the network is
    // the only thing worth asserting on.
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("does not geocode an address that is only whitespace", async () => {
    denyGps();

    await expect(
      resolveCoords({ address: "   ", saved: UMD, unavailableMessage: MESSAGE })
    ).resolves.toEqual({ ...UMD, source: "saved" });

    expect(mockLocation.geocodeAsync).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
