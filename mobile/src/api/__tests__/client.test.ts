/**
 * Tests for the API client's request layer.
 *
 * Two things are worth locking down here, and neither is obvious from reading
 * the call sites:
 *
 * 1. Query strings are built by hand. `/jobs/nearby` silently ignored
 *    `radius_km` for the whole life of the app because the client simply never
 *    sent it, and the server's `radius_km or default` fell back to 25km. No
 *    error, no log — the feature just did nothing.
 *
 * 2. A 401 triggers exactly one refresh, shared across concurrent callers. The
 *    server revokes the entire token family when a spent refresh token is
 *    replayed, so two racing refreshes do not merely waste a round trip: the
 *    second one logs the user out.
 */

import {
  ApiError,
  api,
  setAuthToken,
  setRefreshToken,
  setSessionLostHandler,
  setTokenRotationHandler,
} from "../client";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

/** The URL passed to the Nth fetch call. */
function urlOf(call: number): string {
  return (global.fetch as jest.Mock).mock.calls[call][0] as string;
}

/** The RequestInit passed to the Nth fetch call. */
function initOf(call: number): RequestInit {
  return (global.fetch as jest.Mock).mock.calls[call][1] as RequestInit;
}

beforeEach(() => {
  // Module-level auth state outlives individual tests; reset all of it.
  setAuthToken(null);
  setRefreshToken(null);
  setSessionLostHandler(null);
  setTokenRotationHandler(null);
});

describe("query string construction", () => {
  beforeEach(() => {
    global.fetch = jest.fn(async () => jsonResponse([])) as unknown as typeof fetch;
  });

  it("sends radius_km when the worker has a service radius", async () => {
    await api.nearbyJobs(38.98, -76.94, "cleaning", 60);

    // The regression that motivated this file: without this parameter the
    // server falls back to its 25km default and "Widen my radius" does nothing.
    expect(urlOf(0)).toContain("radius_km=60");
  });

  it("omits radius_km entirely when none is supplied", async () => {
    await api.nearbyJobs(38.98, -76.94, "cleaning");

    // Assert the call happened before asserting what it lacked: a "does not
    // contain" check passes trivially against a request that never went out.
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(urlOf(0)).not.toContain("radius_km");
  });

  it.each([
    ["zero", 0],
    ["negative", -5],
    ["NaN", Number.NaN],
    ["Infinity", Number.POSITIVE_INFINITY],
  ])("omits a %s radius rather than sending a value the server rejects", async (_label, value) => {
    await api.nearbyJobs(38.98, -76.94, undefined, value as number);

    // The server declares Query(gt=0). Sending one of these earns a 422, which
    // surfaces as an error banner and an empty list — strictly worse than
    // quietly falling back to the default radius.
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(urlOf(0)).not.toContain("radius_km");
  });

  it("url-encodes the trade so a value with a space cannot break the query", async () => {
    await api.nearbyJobs(38.98, -76.94, "yard work");

    expect(urlOf(0)).toContain("trade=yard%20work");
  });

  it("omits the trade filter when none is chosen", async () => {
    await api.nearbyJobs(38.98, -76.94);

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(urlOf(0)).not.toContain("trade=");
  });

  it("encodes a device token in the path, which can contain []", async () => {
    await api.unregisterDeviceToken("ExponentPushToken[abc123]");

    expect(urlOf(0)).toContain("ExponentPushToken%5Babc123%5D");
  });
});

describe("authorization header", () => {
  beforeEach(() => {
    global.fetch = jest.fn(async () => jsonResponse({})) as unknown as typeof fetch;
  });

  it("omits Authorization entirely when signed out", async () => {
    await api.authProviders();

    expect(initOf(0).headers).not.toHaveProperty("Authorization");
  });

  it("sends the bearer token once signed in", async () => {
    setAuthToken("access-1");
    await api.me();

    expect(initOf(0).headers).toMatchObject({ Authorization: "Bearer access-1" });
  });
});

describe("401 handling and token refresh", () => {
  it("refreshes once, then replays the original request with the new token", async () => {
    setAuthToken("expired");
    setRefreshToken("refresh-1");

    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "access-2", refresh_token: "refresh-2" })
      )
      .mockResolvedValueOnce(jsonResponse({ id: 7 })) as unknown as typeof fetch;

    await expect(api.me()).resolves.toEqual({ id: 7 });

    expect(global.fetch).toHaveBeenCalledTimes(3);
    expect(urlOf(1)).toContain("/auth/refresh");
    // The replay must carry the *new* access token, not the expired one.
    expect(initOf(2).headers).toMatchObject({ Authorization: "Bearer access-2" });
  });

  it("shares one refresh across concurrent 401s", async () => {
    setAuthToken("expired");
    setRefreshToken("refresh-1");

    let refreshCalls = 0;
    let refreshed = false;

    global.fetch = jest.fn(async (url: string) => {
      if (url.includes("/auth/refresh")) {
        refreshCalls += 1;
        refreshed = true;
        return jsonResponse({ access_token: "access-2", refresh_token: "refresh-2" });
      }
      // Every request before the refresh 401s; replays after it succeed.
      return refreshed
        ? jsonResponse({ ok: true })
        : jsonResponse({ detail: "Not authenticated" }, 401);
    }) as unknown as typeof fetch;

    await Promise.all([api.me(), api.myJobs(), api.balance()]);

    // Three screens firing at once must not burn three rotations. The server
    // treats a replayed refresh token as theft and revokes the family.
    expect(refreshCalls).toBe(1);
  });

  it("does not retry forever when the replayed request also 401s", async () => {
    setAuthToken("expired");
    setRefreshToken("refresh-1");

    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "access-2", refresh_token: "refresh-2" })
      )
      .mockResolvedValueOnce(
        jsonResponse({ detail: "Not authenticated" }, 401)
      ) as unknown as typeof fetch;

    await expect(api.me()).rejects.toBeInstanceOf(ApiError);

    // 1 original + 1 refresh + 1 replay. A fourth call would mean the replay
    // itself tried to refresh, which recurses until the stack gives out.
    expect(global.fetch).toHaveBeenCalledTimes(3);
  });

  it("never tries to refresh on an /auth/ path", async () => {
    setRefreshToken("refresh-1");

    global.fetch = jest.fn(async () =>
      jsonResponse({ detail: "Incorrect email or password" }, 401)
    ) as unknown as typeof fetch;

    await expect(api.login("a@b.co", "wrong")).rejects.toThrow(
      "Incorrect email or password"
    );

    // A bad password must surface as a bad password. Refreshing here would
    // spend the refresh token to re-attempt a login that cannot succeed.
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it("reports the session lost when the refresh itself is rejected", async () => {
    setAuthToken("expired");
    setRefreshToken("revoked");

    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "Invalid token" }, 401)) as unknown as typeof fetch;

    const sessionLost = jest.fn();
    setSessionLostHandler(sessionLost);

    await expect(api.me()).rejects.toBeInstanceOf(ApiError);
    expect(sessionLost).toHaveBeenCalledTimes(1);
  });

  it("hands the rotated pair to the persistence handler", async () => {
    setAuthToken("expired");
    setRefreshToken("refresh-1");

    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "access-2", refresh_token: "refresh-2" })
      )
      .mockResolvedValueOnce(jsonResponse({ id: 7 })) as unknown as typeof fetch;

    const rotated = jest.fn();
    setTokenRotationHandler(rotated);

    await api.me();

    // If this never fires, the new refresh token is lost on app restart and the
    // user is signed out the next morning for no visible reason.
    expect(rotated).toHaveBeenCalledWith("access-2", "refresh-2");
  });
});

describe("error translation", () => {
  it("reports an unreachable server as ApiError status 0", async () => {
    global.fetch = jest.fn(async () => {
      throw new TypeError("Network request failed");
    }) as unknown as typeof fetch;

    await expect(api.me()).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining("Cannot reach the server"),
    });
  });

  it("surfaces a plain string detail from the API", async () => {
    global.fetch = jest.fn(async () =>
      jsonResponse({ detail: "Job is already assigned" }, 409)
    ) as unknown as typeof fetch;

    await expect(api.job(1)).rejects.toMatchObject({
      status: 409,
      message: "Job is already assigned",
    });
  });

  it("unwraps FastAPI's 422 validation shape", async () => {
    global.fetch = jest.fn(async () =>
      jsonResponse(
        { detail: [{ loc: ["query", "radius_km"], msg: "Input should be greater than 0" }] },
        422
      )
    ) as unknown as typeof fetch;

    // FastAPI returns `detail` as a list for validation errors. Rendering that
    // array straight into the UI shows "[object Object]".
    await expect(api.job(1)).rejects.toMatchObject({
      status: 422,
      message: "Input should be greater than 0",
    });
  });

  it("falls back to a generic message when the body is not JSON", async () => {
    global.fetch = jest.fn(async () => ({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON");
      },
    })) as unknown as typeof fetch;

    await expect(api.job(1)).rejects.toMatchObject({
      status: 502,
      message: "Request failed (502)",
    });
  });
});
