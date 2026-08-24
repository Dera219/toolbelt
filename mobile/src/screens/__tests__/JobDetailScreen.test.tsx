/**
 * Screen tests for the job detail screen — the largest file in the app and the
 * one where a silent bug costs the most.
 *
 * Two classes of failure are worth the effort here.
 *
 * **Role gating.** Almost every control on this screen is conditional on who is
 * looking and what state the job is in. A mistake does not look like a crash; it
 * looks like a worker being shown a button that accepts their own offer, or a
 * customer being able to mark a job complete. The server enforces its own rules,
 * so the damage is a confusing 403 rather than a real breach — but a UI that
 * offers an action it cannot perform is its own defect.
 *
 * **Money actions.** Accepting an offer captures an authorisation. Accepting the
 * *wrong* offer pays the wrong worker, and nothing about that renders
 * differently, so the identity of the offer being accepted is asserted directly.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import React from "react";

import { ApiError, api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import JobDetailScreen from "../JobDetailScreen";
import { renderScreen } from "./renderScreen";

// `ui.Button` renders `{icon}  {label}` as two children of one Text, so a button
// with an icon has composed text like "▶️  Start job" and an exact-string query
// silently fails to find it. Regex matchers are used for those.
//
// RNTL 14 also made `fireEvent` async, like `render`. An un-awaited press runs
// before React has flushed the previous event, so a handler reads stale state —
// which looks like the handler never ran rather than like a timing problem.

jest.mock("../../api/client", () => ({
  ...jest.requireActual("../../api/client"),
  api: {
    job: jest.fn(),
    jobOffers: jest.fn(),
    jobPayment: jest.fn(),
    jobRatings: jest.fn(),
    jobDispute: jest.fn(),
    acceptOffer: jest.fn(),
    makeOffer: jest.fn(),
    startJob: jest.fn(),
    completeJob: jest.fn(),
    cancelJob: jest.fn(),
    rateJob: jest.fn(),
    openDispute: jest.fn(),
  },
}));

jest.mock("../../auth/AuthContext", () => ({ useAuth: jest.fn() }));

const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useFocusEffect: (callback: () => void) => {
    const React = require("react");
    React.useEffect(callback, [callback]);
  },
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>;

const CUSTOMER = 1;
const WORKER = 2;
const OTHER_WORKER = 3;

function asUser(id: number, mode: "customer" | "worker") {
  mockUseAuth.mockReturnValue({ user: { id }, mode } as never);
}

function aJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 10,
    title: "Deep clean, 2 bed",
    trade: "cleaning",
    status: "open",
    customer_id: CUSTOMER,
    assigned_worker_id: null,
    budget_cents: 8000,
    currency: "USD",
    description: "Kitchen and bathrooms",
    address_text: "College Park MD",
    lat: 38.9869,
    lng: -76.9426,
    scheduled_for: null,
    customer_provides_supplies: false,
    created_at: "2026-08-01T12:00:00Z",
    photos: [],
    ...overrides,
  };
}

function anOffer(id: number, workerId: number, cents: number, status = "pending") {
  return { id, job_id: 10, worker_id: workerId, price_cents: cents, message: "", status };
}

type ScreenProps = React.ComponentProps<typeof JobDetailScreen>;

function props(): ScreenProps {
  // Cast through unknown rather than `never`: `never` cannot be spread, and the
  // navigation object here is a deliberate stub of the few members this screen
  // touches rather than the full navigator surface.
  return {
    route: { params: { jobId: 10 } },
    navigation: { navigate: mockNavigate, goBack: jest.fn(), setOptions: jest.fn() },
  } as unknown as ScreenProps;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.job.mockResolvedValue(aJob() as never);
  mockApi.jobOffers.mockResolvedValue([] as never);
  mockApi.jobPayment.mockResolvedValue(null as never);
  mockApi.jobRatings.mockResolvedValue(null as never);
  mockApi.jobDispute.mockResolvedValue(null as never);
  asUser(CUSTOMER, "customer");
});

describe("accepting an offer", () => {
  it("accepts the offer that was tapped, not merely the first one", async () => {
    mockApi.jobOffers.mockResolvedValue([
      anOffer(101, WORKER, 7000),
      anOffer(102, OTHER_WORKER, 9000),
    ] as never);
    mockApi.acceptOffer.mockResolvedValue(aJob({ status: "assigned" }) as never);

    await renderScreen(<JobDetailScreen {...props()} />);
    await screen.findAllByText("Accept & book");

    // Tap the SECOND offer. Accepting the wrong one books the wrong worker at
    // the wrong price, and the screen looks identical either way.
    await fireEvent.press(screen.getAllByText("Accept & book")[1]);

    await waitFor(() => expect(mockApi.acceptOffer).toHaveBeenCalledWith(102));
    expect(mockApi.acceptOffer).not.toHaveBeenCalledWith(101);
  });

  it("does not offer to accept an offer that is no longer pending", async () => {
    mockApi.jobOffers.mockResolvedValue([anOffer(101, WORKER, 7000, "rejected")] as never);

    await renderScreen(<JobDetailScreen {...props()} />);
    await screen.findByText("Offers");

    expect(screen.queryByText("Accept & book")).toBeNull();
  });

  it("surfaces a refusal instead of appearing to have booked", async () => {
    mockApi.jobOffers.mockResolvedValue([anOffer(101, WORKER, 7000)] as never);
    mockApi.acceptOffer.mockRejectedValue(new ApiError(409, "Job is already assigned"));

    await renderScreen(<JobDetailScreen {...props()} />);
    await fireEvent.press(await screen.findByText("Accept & book"));

    expect(await screen.findByText("Job is already assigned")).toBeTruthy();
  });
});

describe("who is shown which action", () => {
  it("does not offer the customer a way to accept offers on someone else's job", async () => {
    mockApi.job.mockResolvedValue(aJob({ customer_id: 999 }) as never);
    mockApi.jobOffers.mockResolvedValue([anOffer(101, WORKER, 7000)] as never);

    await renderScreen(<JobDetailScreen {...props()} />);
    await screen.findByText("Deep clean, 2 bed");

    expect(screen.queryByText("Accept & book")).toBeNull();
  });

  it("lets a worker send an offer on an open job", async () => {
    asUser(WORKER, "worker");

    await renderScreen(<JobDetailScreen {...props()} />);

    expect(await screen.findByText("Send offer")).toBeTruthy();
  });

  it("does not invite a second offer from a worker who already made one", async () => {
    asUser(WORKER, "worker");
    mockApi.jobOffers.mockResolvedValue([anOffer(101, WORKER, 7000)] as never);

    await renderScreen(<JobDetailScreen {...props()} />);
    await screen.findByText("Deep clean, 2 bed");

    expect(screen.queryByText("Send offer")).toBeNull();
  });

  it("shows Start only to the assigned worker", async () => {
    asUser(WORKER, "worker");
    mockApi.job.mockResolvedValue(
      aJob({ status: "assigned", assigned_worker_id: WORKER }) as never
    );

    await renderScreen(<JobDetailScreen {...props()} />);

    expect(await screen.findByText(/Start job/)).toBeTruthy();
  });

  it("does not show Start to a worker who was not assigned", async () => {
    asUser(OTHER_WORKER, "worker");
    mockApi.job.mockResolvedValue(
      aJob({ status: "assigned", assigned_worker_id: WORKER }) as never
    );

    await renderScreen(<JobDetailScreen {...props()} />);
    await screen.findByText("Deep clean, 2 bed");

    expect(screen.queryByText(/Start job/)).toBeNull();
  });

  it("offers completion only once the job is under way", async () => {
    asUser(WORKER, "worker");
    mockApi.job.mockResolvedValue(
      aJob({ status: "in_progress", assigned_worker_id: WORKER }) as never
    );
    mockApi.completeJob.mockResolvedValue(aJob({ status: "completed" }) as never);

    await renderScreen(<JobDetailScreen {...props()} />);
    await fireEvent.press(await screen.findByText(/Mark completed/));

    // Completion is what captures the authorisation, so it must not be
    // reachable from a job that never started.
    await waitFor(() => expect(mockApi.completeJob).toHaveBeenCalledWith(10));
  });
});

describe("sending an offer", () => {
  beforeEach(() => asUser(WORKER, "worker"));

  it("converts the typed price to cents", async () => {
    mockApi.makeOffer.mockResolvedValue(anOffer(1, WORKER, 7250) as never);

    await renderScreen(<JobDetailScreen {...props()} />);
    await fireEvent.changeText(await screen.findByPlaceholderText("110.00"), "72.50");
    await fireEvent.press(screen.getByText("Send offer"));

    await waitFor(() => expect(mockApi.makeOffer).toHaveBeenCalledWith(10, 7250, ""));
  });

  it("refuses a price that is not a number, without calling the API", async () => {
    await renderScreen(<JobDetailScreen {...props()} />);
    await fireEvent.changeText(await screen.findByPlaceholderText("110.00"), "abc");
    await fireEvent.press(screen.getByText("Send offer"));

    expect(await screen.findByText("Enter a valid price")).toBeTruthy();
    expect(mockApi.makeOffer).not.toHaveBeenCalled();
  });

  it("refuses a zero price", async () => {
    await renderScreen(<JobDetailScreen {...props()} />);
    await fireEvent.changeText(await screen.findByPlaceholderText("110.00"), "0");
    await fireEvent.press(screen.getByText("Send offer"));

    expect(await screen.findByText("Enter a valid price")).toBeTruthy();
    expect(mockApi.makeOffer).not.toHaveBeenCalled();
  });
});

describe("resilience of the secondary loads", () => {
  it("still renders the job when payment, ratings and dispute all fail", async () => {
    // These four run under Promise.allSettled on purpose: a job with no payment
    // yet legitimately 404s, and letting that blank the screen would make a
    // normal state look like an outage.
    mockApi.jobPayment.mockRejectedValue(new ApiError(404, "no payment"));
    mockApi.jobRatings.mockRejectedValue(new ApiError(404, "no ratings"));
    mockApi.jobDispute.mockRejectedValue(new ApiError(404, "no dispute"));
    mockApi.jobOffers.mockRejectedValue(new ApiError(403, "not yours"));

    await renderScreen(<JobDetailScreen {...props()} />);

    expect(await screen.findByText("Deep clean, 2 bed")).toBeTruthy();
    expect(screen.queryByText("Could not load job")).toBeNull();
  });

  it("reports a failure to load the job itself", async () => {
    mockApi.job.mockRejectedValue(new ApiError(404, "Job not found"));

    await renderScreen(<JobDetailScreen {...props()} />);

    expect(await screen.findByText("Job not found")).toBeTruthy();
  });
});
