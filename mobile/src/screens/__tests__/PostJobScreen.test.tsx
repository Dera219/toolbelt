/**
 * Screen tests for posting a job.
 *
 * The invariant worth defending here is stated in `location.ts`: the API matches
 * workers to jobs with a bounding box plus haversine, so **a job posted without
 * coordinates is invisible to everybody**. It does not error, it does not look
 * wrong, and the customer waits for offers that can never arrive. Every test
 * below exists because of that.
 *
 * Note what this screen does *not* do: it never asks for coordinates. It asks
 * for an address it already required, and resolves that — so the failure mode is
 * not a missing field but a silent resolution failure, which is precisely the
 * kind that ships.
 *
 * See JobDetailScreen.test.tsx for the harness facts these tests rely on: RNTL
 * 14's `render` and `fireEvent` are both async, and `ui.Button` composes an icon
 * into its label so iconed buttons need a regex matcher.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import React from "react";

import { ApiError, api } from "../../api/client";
import { resolveCoords } from "../../location";
import PostJobScreen from "../customer/PostJobScreen";
import { renderScreen } from "./renderScreen";

jest.mock("../../api/client", () => ({
  ...jest.requireActual("../../api/client"),
  api: { createJob: jest.fn(), uploadJobPhoto: jest.fn() },
}));

jest.mock("../../location", () => ({ resolveCoords: jest.fn() }));

const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
  useFocusEffect: (callback: () => void) => {
    const React = require("react");
    React.useEffect(callback, [callback]);
  },
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockResolveCoords = resolveCoords as jest.MockedFunction<typeof resolveCoords>;


const UMD = { lat: 38.9869, lng: -76.9426 };

/** Fill the two fields the screen requires before it will submit. */
async function fillRequired(title = "Deep clean 2BR", address = "College Park, MD") {
  await fireEvent.changeText(screen.getByPlaceholderText("Deep clean 2BR apartment"), title);
  await fireEvent.changeText(screen.getByPlaceholderText("College Park, MD"), address);
}

beforeEach(() => {
  jest.clearAllMocks();
  mockResolveCoords.mockResolvedValue({ ...UMD, source: "gps" });
  mockApi.createJob.mockResolvedValue({ id: 42 } as never);
  mockApi.uploadJobPhoto.mockResolvedValue(undefined as never);
});

describe("the coordinates a job is posted with", () => {
  it("posts the resolved coordinates, never a job without them", async () => {
    await renderScreen(<PostJobScreen />);
    await fillRequired();
    await fireEvent.press(screen.getByText(/Post job/));

    await waitFor(() => expect(mockApi.createJob).toHaveBeenCalled());
    const body = mockApi.createJob.mock.calls[0][0];

    // A job whose lat/lng did not survive the trip is invisible to every worker
    // and reports no error to anyone.
    expect(body.lat).toBe(UMD.lat);
    expect(body.lng).toBe(UMD.lng);
  });

  it("hands the typed address to the resolver as its fallback", async () => {
    await renderScreen(<PostJobScreen />);
    await fillRequired("Deep clean 2BR", "7600 Baltimore Ave, College Park MD");
    await fireEvent.press(screen.getByText(/Post job/));

    // The address is required anyway, so it doubles as the fallback when
    // location permission is denied. If it is not passed, a customer with GPS
    // off cannot post at all.
    await waitFor(() =>
      expect(mockResolveCoords).toHaveBeenCalledWith(
        expect.objectContaining({ address: "7600 Baltimore Ave, College Park MD" })
      )
    );
  });

  it("does not create a job at all when the address cannot be resolved", async () => {
    mockResolveCoords.mockRejectedValue(
      new ApiError(0, "We couldn't pin that address. Allow location access, or add more detail.")
    );

    await renderScreen(<PostJobScreen />);
    await fillRequired();
    await fireEvent.press(screen.getByText(/Post job/));

    // The critical assertion. Posting anyway would produce a job nobody can
    // find, which is strictly worse than refusing to post.
    expect(await screen.findByText(/couldn't pin that address/)).toBeTruthy();
    expect(mockApi.createJob).not.toHaveBeenCalled();
  });
});

describe("the budget field", () => {
  it("sends no budget when the field is left empty", async () => {
    await renderScreen(<PostJobScreen />);
    await fillRequired();
    await fireEvent.press(screen.getByText(/Post job/));

    await waitFor(() => expect(mockApi.createJob).toHaveBeenCalled());
    // Null means "open to offers", which is a real choice — not zero.
    expect(mockApi.createJob.mock.calls[0][0].budget_cents).toBeNull();
  });

  it("converts a typed budget to cents", async () => {
    await renderScreen(<PostJobScreen />);
    await fillRequired();
    await fireEvent.changeText(screen.getByPlaceholderText("120.00"), "85.50");
    await fireEvent.press(screen.getByText(/Post job/));

    await waitFor(() => expect(mockApi.createJob).toHaveBeenCalled());
    expect(mockApi.createJob.mock.calls[0][0].budget_cents).toBe(8550);
  });

  it("refuses a budget that is not a positive amount", async () => {
    await renderScreen(<PostJobScreen />);
    await fillRequired();
    await fireEvent.changeText(screen.getByPlaceholderText("120.00"), "-5");
    await fireEvent.press(screen.getByText(/Post job/));

    expect(await screen.findByText("Budget must be a positive amount")).toBeTruthy();
    expect(mockApi.createJob).not.toHaveBeenCalled();
  });
});

describe("what happens after the job exists", () => {
  it("navigates to the job it just created", async () => {
    mockApi.createJob.mockResolvedValue({ id: 77 } as never);

    await renderScreen(<PostJobScreen />);
    await fillRequired();
    await fireEvent.press(screen.getByText(/Post job/));

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith("JobDetail", { jobId: 77 })
    );
  });

  it("reports a refusal from the API rather than appearing to have posted", async () => {
    mockApi.createJob.mockRejectedValue(new ApiError(422, "Title is too short"));

    await renderScreen(<PostJobScreen />);
    await fillRequired();
    await fireEvent.press(screen.getByText(/Post job/));

    expect(await screen.findByText("Title is too short")).toBeTruthy();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("trims what it sends, so whitespace does not become the title", async () => {
    await renderScreen(<PostJobScreen />);
    await fillRequired("  Deep clean 2BR  ", "  College Park, MD  ");
    await fireEvent.press(screen.getByText(/Post job/));

    await waitFor(() => expect(mockApi.createJob).toHaveBeenCalled());
    const body = mockApi.createJob.mock.calls[0][0];
    expect(body.title).toBe("Deep clean 2BR");
    expect(body.address_text).toBe("College Park, MD");
  });
});
