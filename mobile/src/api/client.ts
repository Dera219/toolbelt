import { API_URL } from "../config";
import type {
  Balance,
  BillingProfile,
  Dispute,
  DisputeReason,
  Job,
  JobRatings,
  Message,
  NearbyJob,
  Offer,
  Payment,
  PayoutAccount,
  Role,
  User,
  WorkerProfile,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

let authToken: string | null = null;
let refreshToken: string | null = null;
let onSessionLost: (() => void) | null = null;
let refreshInFlight: Promise<boolean> | null = null;

export const setAuthToken = (token: string | null) => {
  authToken = token;
};

export const setRefreshToken = (token: string | null) => {
  refreshToken = token;
};

/** Called when the session cannot be renewed and the user must sign in again. */
export const setSessionLostHandler = (handler: (() => void) | null) => {
  onSessionLost = handler;
};

/** Persisted by AuthContext whenever rotation produces a new refresh token. */
let onTokensRotated: ((access: string, refresh: string) => void) | null = null;
export const setTokenRotationHandler = (
  handler: ((access: string, refresh: string) => void) | null
) => {
  onTokensRotated = handler;
};

/**
 * Exchange the refresh token for a new pair. Concurrent 401s share one attempt,
 * so a screen firing three requests does not burn three rotations — and since
 * the server revokes the whole family when a spent token is replayed, racing
 * refreshes would otherwise log the user out.
 */
async function refreshSession(): Promise<boolean> {
  if (refreshToken == null) return false;
  if (refreshInFlight != null) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const resp = await fetch(`${API_URL}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!resp.ok) return false;
      const data = (await resp.json()) as { access_token: string; refresh_token: string };
      authToken = data.access_token;
      refreshToken = data.refresh_token;
      onTokensRotated?.(data.access_token, data.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is the API running?");
  }
  // Access tokens live 15 minutes; renew once and replay the request rather
  // than bouncing the user to the login screen mid-task.
  if (resp.status === 401 && retry && refreshToken != null && !path.startsWith("/auth/")) {
    if (await refreshSession()) return request<T>(method, path, body, false);
    onSessionLost?.();
  }
  if (!resp.ok) {
    let detail = `Request failed (${resp.status})`;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail) && data.detail[0]?.msg)
        detail = data.detail[0].msg;
    } catch {
      // keep the generic message
    }
    throw new ApiError(resp.status, detail);
  }
  // 204 No Content carries an empty body, and `resp.json()` on one throws
  // SyntaxError. Three routes return it — /auth/logout and both device-token
  // routes — so every caller typed `request<void>` was rejecting on a request
  // that had already succeeded.
  //
  // The damage was not where it looked. `logout` wraps its call in `.catch(() => {})`
  // so it appeared fine, but `registerForPush` does not: the throw skipped
  // `cachedToken = token`, leaving it null forever, which made `unregisterPush`
  // (guarded on that value) a permanent no-op. Logging out never released the
  // device server-side, so the next person to sign in on the same phone kept
  // receiving the previous user's job alerts — the exact thing `logout` unregisters
  // to prevent.
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  // auth & identity
  register: (body: { email: string; password: string; full_name: string; role: Role }) =>
    request<User>("POST", "/auth/register", body),
  login: (email: string, password: string) =>
    request<{ access_token: string; refresh_token: string }>("POST", "/auth/login", {
      email,
      password,
    }),
  logout: (refresh_token: string) => request<void>("POST", "/auth/logout", { refresh_token }),
  authProviders: () => request<{ providers: string[] }>("GET", "/auth/providers"),
  socialSignIn: (provider: string, id_token: string, role?: string) =>
    request<{ access_token: string; refresh_token: string }>("POST", "/auth/social", {
      provider,
      id_token,
      ...(role ? { role } : {}),
    }),
  revokeAllSessions: () => request<{ revoked: number }>("POST", "/me/sessions/revoke-all"),
  me: () => request<User>("GET", "/me"),
  requestPhoneCode: (phone: string) =>
    request<{ detail: string }>("POST", "/me/phone/request-verification", { phone }),
  verifyPhone: (code: string) => request<User>("POST", "/me/phone/verify", { code }),

  // push notifications
  registerDeviceToken: (token: string, platform: string) =>
    request<void>("POST", "/me/device-tokens", { token, platform }),
  unregisterDeviceToken: (token: string) =>
    request<void>("DELETE", `/me/device-tokens/${encodeURIComponent(token)}`),

  // worker profile & vetting
  getWorkerProfile: () => request<WorkerProfile>("GET", "/me/worker-profile"),
  saveWorkerProfile: (body: {
    trade: string;
    bio: string;
    hourly_rate_cents: number;
    base_lat: number;
    base_lng: number;
    service_radius_km: number;
    is_available: boolean;
    has_own_tools: boolean;
    has_vehicle: boolean;
  }) => request<WorkerProfile>("PUT", "/me/worker-profile", body),
  submitVetting: () => request<WorkerProfile>("POST", "/me/worker-profile/submit-vetting"),

  // jobs
  createJob: (body: {
    trade: string;
    title: string;
    description: string;
    lat: number;
    lng: number;
    address_text: string;
    budget_cents: number | null;
    customer_provides_supplies: boolean;
  }) => request<Job>("POST", "/jobs", body),
  myJobs: () => request<Job[]>("GET", "/jobs/mine"),
  nearbyJobs: (lat: number, lng: number, trade?: string, radiusKm?: number) => {
    // Omit a missing or nonsensical radius rather than sending it: the API
    // rejects radius_km <= 0, and falling back to its default beats a 422 that
    // would empty the whole screen. The server caps the value it accepts.
    const radiusParam =
      radiusKm != null && Number.isFinite(radiusKm) && radiusKm > 0
        ? `&radius_km=${radiusKm}`
        : "";
    return request<NearbyJob[]>(
      "GET",
      `/jobs/nearby?lat=${lat}&lng=${lng}${trade ? `&trade=${encodeURIComponent(trade)}` : ""}${radiusParam}`
    );
  },
  job: (id: number) => request<Job>("GET", `/jobs/${id}`),
  startJob: (id: number) => request<Job>("POST", `/jobs/${id}/start`),
  completeJob: (id: number) => request<Job>("POST", `/jobs/${id}/complete`),
  cancelJob: (id: number) => request<Job>("POST", `/jobs/${id}/cancel`),

  // photos
  uploadJobPhoto: async (jobId: number, uri: string): Promise<void> => {
    const name = uri.split("/").pop() ?? "photo.jpg";
    const ext = name.split(".").pop()?.toLowerCase();
    const type = ext === "png" ? "image/png" : ext === "webp" ? "image/webp" : "image/jpeg";
    const form = new FormData();
    // React Native FormData accepts {uri, name, type} file descriptors.
    form.append("file", { uri, name, type } as unknown as Blob);
    const resp = await fetch(`${API_URL}/jobs/${jobId}/photos`, {
      method: "POST",
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
      body: form,
    });
    if (!resp.ok) throw new ApiError(resp.status, "Photo upload failed");
  },

  // offers
  jobOffers: (jobId: number) => request<Offer[]>("GET", `/jobs/${jobId}/offers`),
  makeOffer: (jobId: number, price_cents: number, message: string) =>
    request<Offer>("POST", `/jobs/${jobId}/offers`, { price_cents, message }),
  acceptOffer: (offerId: number) => request<Offer>("POST", `/offers/${offerId}/accept`),

  // chat
  messages: (jobId: number, workerId: number) =>
    request<Message[]>("GET", `/jobs/${jobId}/threads/${workerId}/messages`),
  sendMessage: (jobId: number, workerId: number, body: string) =>
    request<Message>("POST", `/jobs/${jobId}/threads/${workerId}/messages`, { body }),

  // ratings
  rateJob: (jobId: number, stars: number, comment: string) =>
    request<unknown>("POST", `/jobs/${jobId}/ratings`, { stars, comment }),
  jobRatings: (jobId: number) => request<JobRatings>("GET", `/jobs/${jobId}/ratings`),

  // disputes
  openDispute: (jobId: number, reason: DisputeReason, detail: string) =>
    request<Dispute>("POST", `/jobs/${jobId}/dispute`, { reason, detail }),
  jobDispute: (jobId: number) => request<Dispute>("GET", `/jobs/${jobId}/dispute`),

  // payments
  getBillingProfile: () => request<BillingProfile>("GET", "/me/billing/profile"),
  startCardSetup: () =>
    request<{
      setup_ref: string;
      setup_intent_client_secret: string;
      customer_ephemeral_key_secret: string;
      customer_ref: string;
      publishable_key: string | null;
    }>("POST", "/me/billing/card-setup"),
  startCardSetupSession: (return_url: string) =>
    request<{ url: string }>("POST", "/me/billing/card-setup-session", { return_url }),
  confirmCard: (setup_ref?: string) =>
    request<BillingProfile>("POST", "/me/billing/confirm-card", { setup_ref: setup_ref ?? null }),
  setPaymentMethod: (ref: string) =>
    request<BillingProfile>("POST", "/me/payment-method", { payment_method_ref: ref }),
  createPayoutAccount: () => request<PayoutAccount>("POST", "/me/payout-account"),
  getPayoutAccount: () => request<PayoutAccount>("GET", "/me/payout-account"),
  balance: () => request<Balance>("GET", "/me/balance"),
  jobPayment: (jobId: number) => request<Payment>("GET", `/jobs/${jobId}/payment`),
};

export function money(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
}
