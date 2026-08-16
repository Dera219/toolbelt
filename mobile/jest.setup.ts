/**
 * Shared test setup.
 *
 * The default `fetch` throws. Every test that touches the network must say so
 * explicitly — a test that silently reaches the real API is worse than no test,
 * because it passes on a developer's machine and fails in CI for a reason that
 * has nothing to do with the code.
 */

beforeEach(() => {
  global.fetch = jest.fn(() => {
    throw new Error(
      "Unmocked fetch. Assign global.fetch in the test that needs it."
    );
  }) as unknown as typeof fetch;
});

/** A minimal stand-in for the parts of Response that api/client.ts reads. */
export function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}
