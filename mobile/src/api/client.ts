import { API_URL } from "../config";
import type {
  Balance,
  BillingProfile,
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
export const setAuthToken = (token: string | null) => {
  authToken = token;
};

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
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
  return (await resp.json()) as T;
}

export const api = {
  // auth & identity
  register: (body: { email: string; password: string; full_name: string; role: Role }) =>
    request<User>("POST", "/auth/register", body),
  login: (email: string, password: string) =>
    request<{ access_token: string }>("POST", "/auth/login", { email, password }),
  me: () => request<User>("GET", "/me"),
  requestPhoneCode: (phone: string) =>
    request<{ detail: string }>("POST", "/me/phone/request-verification", { phone }),
  verifyPhone: (code: string) => request<User>("POST", "/me/phone/verify", { code }),

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
  nearbyJobs: (lat: number, lng: number, trade?: string) =>
    request<NearbyJob[]>(
      "GET",
      `/jobs/nearby?lat=${lat}&lng=${lng}${trade ? `&trade=${trade}` : ""}`
    ),
  job: (id: number) => request<Job>("GET", `/jobs/${id}`),
  startJob: (id: number) => request<Job>("POST", `/jobs/${id}/start`),
  completeJob: (id: number) => request<Job>("POST", `/jobs/${id}/complete`),
  cancelJob: (id: number) => request<Job>("POST", `/jobs/${id}/cancel`),

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

  // payments
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
