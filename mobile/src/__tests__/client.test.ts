/**
 * Tests for the shared `request` helper in api/client.ts.
 *
 * This file exists because the helper had no tests at all, and the gap hid a
 * real defect: `request` ended in an unconditional `resp.json()`, which throws
 * `SyntaxError` on the empty body of a 204. Every route typed `request<void>`
 * therefore rejected on a call that had already succeeded server-side.
 *
 * The push tests did not catch it because they mock `api.registerDeviceToken`
 * with `mockResolvedValue(undefined)` — a mock more forgiving than production,
 * which is the failure mode a mock is most likely to have. Here `fetch` is
 * mocked instead, so the real helper runs.
 */

import { ApiError, api, setAuthToken } from "../api/client";

const jsonResponse = (status: number, body: unknown) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }) as unknown as Response;

/** A real 204: no body at all, so `json()` rejects exactly as the browser's does. */
const noContentResponse = () =>
  ({
    ok: true,
    status: 204,
    json: async () => {
      throw new SyntaxError("JSON Parse error: Unexpected end of input");
    },
  }) as unknown as Response;

describe("request", () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    setAuthToken("token-123");
  });

  afterEach(() => {
    setAuthToken(null);
    jest.resetAllMocks();
  });

  describe("204 No Content", () => {
    it("resolves instead of throwing a JSON parse error", async () => {
      fetchMock.mockResolvedValue(noContentResponse());

      await expect(api.registerDeviceToken("ExpoPushToken[x]", "android")).resolves.toBeUndefined();
    });

    it("lets logout complete", async () => {
      fetchMock.mockResolvedValue(noContentResponse());

      await expect(api.logout("refresh-token")).resolves.toBeUndefined();
    });

    it("lets a device token be released", async () => {
      fetchMock.mockResolvedValue(noContentResponse());

      await expect(api.unregisterDeviceToken("ExpoPushToken[x]")).resolves.toBeUndefined();
    });

    it("never reads the body, so a throwing json() cannot matter", async () => {
      const json = jest.fn(async () => {
        throw new SyntaxError("JSON Parse error: Unexpected end of input");
      });
      fetchMock.mockResolvedValue({ ok: true, status: 204, json } as unknown as Response);

      await api.registerDeviceToken("ExpoPushToken[x]", "android");

      expect(json).not.toHaveBeenCalled();
    });
  });

  describe("responses that do have a body", () => {
    it("still parses a 200", async () => {
      fetchMock.mockResolvedValue(jsonResponse(200, { id: 7, email: "a@b.com" }));

      await expect(api.me()).resolves.toEqual({ id: 7, email: "a@b.com" });
    });

    it("still raises ApiError with the server's detail", async () => {
      fetchMock.mockResolvedValue(jsonResponse(400, { detail: "Token already registered" }));

      await expect(api.registerDeviceToken("ExpoPushToken[x]", "android")).rejects.toThrow(
        new ApiError(400, "Token already registered"),
      );
    });

    it("reports an unreachable server rather than a parse failure", async () => {
      fetchMock.mockRejectedValue(new TypeError("Network request failed"));

      await expect(api.me()).rejects.toBeInstanceOf(ApiError);
    });
  });
});
