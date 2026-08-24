/**
 * Screen tests for sign-in and registration.
 *
 * These are the two screens every user meets first, and the only ones where a
 * failure means nobody gets in at all. What is worth pinning is not the happy
 * path — it is that credentials are passed through unmangled, and that a
 * rejection is shown rather than swallowed.
 *
 * The trimming assertions look fussy and are not. An email with a trailing space
 * from an autofill or a paste fails server-side lookup with "incorrect email or
 * password", which sends the user to reset a password that was never wrong.
 * Passwords are deliberately *not* trimmed: a leading or trailing space is a
 * legitimate character, and silently removing it locks the user out of an
 * account they created with it.
 *
 * Harness facts these rely on are documented in JobDetailScreen.test.tsx.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react-native";
import React from "react";

import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import LoginScreen from "../LoginScreen";
import RegisterScreen from "../RegisterScreen";
import { renderScreen } from "./renderScreen";

jest.mock("../../auth/AuthContext", () => ({ useAuth: jest.fn() }));

const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
}));

// SocialButtons is a DEFAULT export; mocking it as a named one yields an object
// where a component is expected, and React reports only "element type is
// invalid" without naming the import.
jest.mock("../../auth/SocialButtons", () => ({
  __esModule: true,
  default: () => null,
}));

// Unlike PostJob and WorkerProfileEdit, these two are navigator screens and do
// take props. Only `navigation.navigate` is ever reached, so the rest is a stub.
function navProps<T>(): T {
  return {
    navigation: { navigate: mockNavigate, goBack: jest.fn(), setOptions: jest.fn() },
    route: { key: "k", name: "Login", params: undefined },
  } as unknown as T;
}

const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>;
const login = jest.fn();
const register = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  login.mockResolvedValue(undefined);
  register.mockResolvedValue(undefined);
  mockUseAuth.mockReturnValue({ login, register, user: null } as never);
});

describe("logging in", () => {
  it("passes the typed credentials through", async () => {
    await renderScreen(<LoginScreen {...navProps<React.ComponentProps<typeof LoginScreen>>()} />);

    await fireEvent.changeText(screen.getByPlaceholderText("you@example.com"), "a@b.co");
    await fireEvent.changeText(screen.getByPlaceholderText("••••••••••"), "hunter2hunter2");
    await fireEvent.press(screen.getByText(/Log in/));

    await waitFor(() => expect(login).toHaveBeenCalledWith("a@b.co", "hunter2hunter2"));
  });

  it("trims the email but never the password", async () => {
    await renderScreen(<LoginScreen {...navProps<React.ComponentProps<typeof LoginScreen>>()} />);

    await fireEvent.changeText(screen.getByPlaceholderText("you@example.com"), "  a@b.co  ");
    await fireEvent.changeText(screen.getByPlaceholderText("••••••••••"), " spaced pass ");
    await fireEvent.press(screen.getByText(/Log in/));

    // A pasted email with whitespace fails lookup and reads as a wrong password,
    // sending the user to reset something that was never wrong. A password's
    // spaces, by contrast, are real characters the account may depend on.
    await waitFor(() =>
      expect(login).toHaveBeenCalledWith("a@b.co", " spaced pass ")
    );
  });

  it("shows the reason a sign-in was refused", async () => {
    login.mockRejectedValue(new ApiError(401, "Incorrect email or password"));

    await renderScreen(<LoginScreen {...navProps<React.ComponentProps<typeof LoginScreen>>()} />);
    await fireEvent.press(screen.getByText(/Log in/));

    expect(await screen.findByText("Incorrect email or password")).toBeTruthy();
  });

  it("does not leave the button spinning after a failure", async () => {
    login.mockRejectedValue(new ApiError(401, "Incorrect email or password"));

    await renderScreen(<LoginScreen {...navProps<React.ComponentProps<typeof LoginScreen>>()} />);
    await fireEvent.press(screen.getByText(/Log in/));
    await screen.findByText("Incorrect email or password");

    // The finally block exists for this. A permanently loading button is
    // indistinguishable from a hung app, and the user force-quits.
    await fireEvent.press(screen.getByText(/Log in/));
    await waitFor(() => expect(login).toHaveBeenCalledTimes(2));
  });
});

describe("registering", () => {
  async function fill(password = "longenoughpw") {
    await fireEvent.changeText(screen.getByPlaceholderText("Chidera Onyebu"), "A Person");
    await fireEvent.changeText(screen.getByPlaceholderText("you@example.com"), "a@b.co");
    await fireEvent.changeText(screen.getByPlaceholderText("••••••••••"), password);
  }

  it("registers as a customer by default", async () => {
    await renderScreen(<RegisterScreen />);
    await fill();
    await fireEvent.press(screen.getByText(/Create account/));

    // Role is part of identity from the first request; defaulting to the wrong
    // one sends a hirer into the worker vetting flow.
    await waitFor(() =>
      expect(register).toHaveBeenCalledWith("a@b.co", "longenoughpw", "A Person", "customer")
    );
  });

  it("registers with the chosen role", async () => {
    await renderScreen(<RegisterScreen />);
    await fill();
    await fireEvent.press(screen.getByText(/Find work/));
    await fireEvent.press(screen.getByText(/Create account/));

    await waitFor(() =>
      expect(register).toHaveBeenCalledWith("a@b.co", "longenoughpw", "A Person", "worker")
    );
  });

  it("reports why a registration was rejected", async () => {
    register.mockRejectedValue(new ApiError(409, "Email already registered"));

    await renderScreen(<RegisterScreen />);
    await fill();
    await fireEvent.press(screen.getByText(/Create account/));

    expect(await screen.findByText("Email already registered")).toBeTruthy();
  });
});
