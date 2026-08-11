export type Role = "customer" | "worker" | "both";
export type JobStatus =
  | "open"
  | "assigned"
  | "in_progress"
  | "completed"
  | "cancelled"
  | "disputed";
export type OfferStatus = "pending" | "accepted" | "declined" | "withdrawn" | "expired";
export type VettingStatus = "unverified" | "pending" | "verified" | "rejected";
export type PaymentStatus = "authorized" | "captured" | "paid_out" | "released" | "refunded";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  locale: string;
  currency: string;
  phone: string | null;
  phone_verified: boolean;
}

export interface WorkerProfile {
  user_id: number;
  trade: string;
  bio: string;
  hourly_rate_cents: number;
  base_lat: number;
  base_lng: number;
  service_radius_km: number;
  is_available: boolean;
  has_own_tools: boolean;
  has_vehicle: boolean;
  vetting_status: VettingStatus;
  rating_avg: number | null;
  jobs_completed: number;
}

export interface JobPhoto {
  id: number;
  url: string;
}

export interface Job {
  id: number;
  customer_id: number;
  trade: string;
  title: string;
  description: string;
  lat: number;
  lng: number;
  address_text: string;
  scheduled_for: string | null;
  budget_cents: number | null;
  currency: string;
  customer_provides_supplies: boolean;
  status: JobStatus;
  assigned_worker_id: number | null;
  created_at: string;
  photos: JobPhoto[];
}

export interface NearbyJob extends Job {
  distance_km: number;
}

export interface Offer {
  id: number;
  job_id: number;
  worker_id: number;
  price_cents: number;
  message: string;
  status: OfferStatus;
  created_at: string;
}

export interface Message {
  id: number;
  job_id: number;
  worker_id: number;
  sender_id: number;
  body: string;
  created_at: string;
}

export interface Rating {
  id: number;
  job_id: number;
  rater_id: number;
  ratee_id: number;
  stars: number;
  comment: string;
  created_at: string;
}

export interface JobRatings {
  mine: Rating | null;
  other: Rating | null;
  other_submitted: boolean;
}

export interface Payment {
  id: number;
  job_id: number;
  customer_id: number;
  worker_id: number;
  amount_cents: number;
  platform_fee_cents: number;
  worker_net_cents: number;
  refunded_cents: number;
  currency: string;
  status: PaymentStatus;
  created_at: string;
}

export interface Balance {
  balance_cents: number;
  currency: string;
}

export interface PayoutAccount {
  user_id: number;
  provider_account_ref: string;
  payouts_enabled: boolean;
  onboarding_url?: string;
}

export interface BillingProfile {
  user_id: number;
  default_payment_method_ref: string | null;
}

export type DisputeReason =
  | "work_not_done"
  | "quality"
  | "damage"
  | "no_show"
  | "overcharged"
  | "unsafe"
  | "other";

export interface Dispute {
  id: number;
  job_id: number;
  opened_by: number;
  against_id: number;
  reason: DisputeReason;
  detail: string;
  status: "open" | "resolved";
  outcome: "dismissed" | "partial_refund" | "full_refund" | null;
  resolution_note: string;
  refunded_cents: number;
  created_at: string;
  resolved_at: string | null;
}
