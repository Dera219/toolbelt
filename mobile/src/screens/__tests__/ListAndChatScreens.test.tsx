/**
 * Screen tests for the two job lists and the in-app chat.
 *
 * These are the lowest-stakes screens in the app, so the tests are narrow and
 * deliberately so. Each covers the one thing that would be quietly wrong rather
 * than obviously broken.
 *
 * MyWorkScreen filters `myJobs()` down to the jobs assigned to *this* worker.
 * The endpoint returns jobs where the user is either party, so a missing filter
 * shows a worker the jobs they posted as a customer, mixed in with their work,
 * with no visible sign of the mistake.
 *
 * ChatScreen polls every four seconds. These tests do not fake the clock and do
 * not need to: the screen clears its own interval on unmount and RNTL unmounts
 * after every test, so nothing leaks. Asserting the poll itself would mean
 * faking timers, which is worth doing only if the polling interval ever becomes
 * something a bug could get wrong.
 *
 * Harness facts these rely on are documented in JobDetailScreen.test.tsx.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import React from "react";

import { ApiError, api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import MyJobsScreen from "../customer/MyJobsScreen";
import ChatScreen from "../ChatScreen";
import MyWorkScreen from "../worker/MyWorkScreen";
import { renderScreen } from "./renderScreen";

jest.mock("../../api/client", () => ({
  ...jest.requireActual("../../api/client"),
  api: {
    myJobs: jest.fn(),
    balance: jest.fn(),
    getWorkerProfile: jest.fn(),
    messages: jest.fn(),
    sendMessage: jest.fn(),
  },
}));

jest.mock("../../auth/AuthContext", () => ({ useAuth: jest.fn() }));

jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: jest.fn() }),
  useFocusEffect: (callback: () => void) => {
    const React = require("react");
    React.useEffect(callback, [callback]);
  },
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>;

const WORKER = 2;

function job(id: number, title: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    title,
    trade: "cleaning",
    status: "open",
    customer_id: 1,
    assigned_worker_id: null,
    budget_cents: 8000,
    currency: "USD",
    description: "",
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

beforeEach(() => {
  jest.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: { id: WORKER }, mode: "worker" } as never);
  mockApi.myJobs.mockResolvedValue([] as never);
  mockApi.balance.mockResolvedValue({ available_cents: 0, pending_cents: 0, currency: "USD" } as never);
  mockApi.getWorkerProfile.mockResolvedValue(null as never);
  mockApi.messages.mockResolvedValue([] as never);
});

describe("the customer's job list", () => {
  it("invites a first job when there are none", async () => {
    await renderScreen(<MyJobsScreen />);

    expect(await screen.findByText("No jobs yet")).toBeTruthy();
  });

  it("keeps showing what it had when a refresh fails", async () => {
    mockApi.myJobs.mockResolvedValueOnce([job(1, "Deep clean")] as never);
    await renderScreen(<MyJobsScreen />);
    await screen.findByText("Deep clean");

    // `setJobs(prev => prev ?? [])` on failure is deliberate: replacing a good
    // list with an empty one tells a customer their jobs are gone.
    mockApi.myJobs.mockRejectedValue(new ApiError(500, "boom"));
    await fireEvent.press(screen.getByText("Deep clean"));

    expect(screen.getByText("Deep clean")).toBeTruthy();
  });
});

describe("the worker's job list", () => {
  it("shows only the jobs assigned to this worker", async () => {
    mockApi.myJobs.mockResolvedValue([
      job(1, "Assigned to me", { assigned_worker_id: WORKER, status: "assigned" }),
      job(2, "Posted by me as a customer", { customer_id: WORKER }),
      job(3, "Someone else's work", { assigned_worker_id: 99, status: "assigned" }),
    ] as never);

    await renderScreen(<MyWorkScreen />);
    await screen.findByText("Assigned to me");

    // myJobs() returns jobs where the user is EITHER party. Without the filter a
    // dual-role user sees their own postings inside their work queue.
    expect(screen.queryByText("Posted by me as a customer")).toBeNull();
    expect(screen.queryByText("Someone else's work")).toBeNull();
  });

  it("still renders when balance and profile are unavailable", async () => {
    mockApi.myJobs.mockResolvedValue([
      job(1, "Assigned to me", { assigned_worker_id: WORKER, status: "assigned" }),
    ] as never);
    mockApi.balance.mockRejectedValue(new ApiError(404, "no balance"));
    mockApi.getWorkerProfile.mockRejectedValue(new ApiError(404, "no profile"));

    // allSettled on purpose: a worker with no profile yet is a normal state.
    await renderScreen(<MyWorkScreen />);

    expect(await screen.findByText("Assigned to me")).toBeTruthy();
  });
});

describe("chat", () => {
  // ChatScreen destructures only `route`, but its Props type still requires
  // `navigation`, and RouteProp carries key and name alongside params.
  const chatProps = {
    route: { key: "chat", name: "Chat", params: { jobId: 10, workerId: WORKER } },
    navigation: { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() },
  } as unknown as React.ComponentProps<typeof ChatScreen>;

  it("sends the typed message and clears the box", async () => {
    mockApi.sendMessage.mockResolvedValue(undefined as never);

    await renderScreen(<ChatScreen {...chatProps} />);
    const box = await screen.findByPlaceholderText("Message…");
    await fireEvent.changeText(box, "On my way");
    await fireEvent.press(screen.getByText(/Send/));

    await waitFor(() => expect(mockApi.sendMessage).toHaveBeenCalledWith(10, WORKER, "On my way"));
    // Not clearing means the next tap sends the same message twice.
    expect(screen.getByPlaceholderText("Message…").props.value).toBe("");
  });

  it("refuses to send whitespace", async () => {
    await renderScreen(<ChatScreen {...chatProps} />);
    await fireEvent.changeText(await screen.findByPlaceholderText("Message…"), "   ");
    await fireEvent.press(screen.getByText(/Send/));

    expect(mockApi.sendMessage).not.toHaveBeenCalled();
  });

  it("keeps the draft when sending fails", async () => {
    mockApi.sendMessage.mockRejectedValue(new ApiError(500, "Could not send"));

    await renderScreen(<ChatScreen {...chatProps} />);
    await fireEvent.changeText(await screen.findByPlaceholderText("Message…"), "Running late");
    await fireEvent.press(screen.getByText(/Send/));

    // Clearing on failure loses what the user wrote and they have to retype it.
    expect(await screen.findByText("Could not send")).toBeTruthy();
    expect(screen.getByPlaceholderText("Message…").props.value).toBe("Running late");
  });
});
