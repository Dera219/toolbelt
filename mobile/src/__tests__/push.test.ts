/**
 * Tests for push registration.
 *
 * The governing rule in push.ts is that push is a convenience and login is not:
 * nothing in here may throw into the caller, because a notification failing is
 * recoverable and a blocked sign-in is not. Most of these tests exist to prove
 * that the failure paths stay quiet, since a regression there does not show up
 * as a missing notification — it shows up as an app nobody can log in to.
 */

import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";

import { api } from "../api/client";
import { currentPushToken, registerForPush, unregisterPush } from "../push";

// `__esModule: true` matters: without it Babel's interop copies the mock's
// properties into a fresh object, so flipping `isDevice` from a test mutates a
// different object than push.ts reads, and the simulator branch never fires.
jest.mock("expo-device", () => ({ __esModule: true, isDevice: true }));

jest.mock("expo-notifications", () => ({
  setNotificationHandler: jest.fn(),
  getPermissionsAsync: jest.fn(),
  requestPermissionsAsync: jest.fn(),
  getExpoPushTokenAsync: jest.fn(),
  setNotificationChannelAsync: jest.fn(),
  addNotificationResponseReceivedListener: jest.fn(() => ({ remove: jest.fn() })),
  AndroidImportance: { HIGH: 4 },
}));

jest.mock("../api/client", () => ({
  api: {
    registerDeviceToken: jest.fn(),
    unregisterDeviceToken: jest.fn(),
  },
}));

const mockNotifications = Notifications as jest.Mocked<typeof Notifications>;
const mockApi = api as jest.Mocked<typeof api>;

const TOKEN = "ExponentPushToken[abc123]";

function granted() {
  mockNotifications.getPermissionsAsync.mockResolvedValue({ status: "granted" } as never);
}

function undetermined() {
  mockNotifications.getPermissionsAsync.mockResolvedValue({
    status: "undetermined",
  } as never);
}

beforeEach(() => {
  jest.clearAllMocks();
  (Device as { isDevice: boolean }).isDevice = true;
  mockNotifications.getExpoPushTokenAsync.mockResolvedValue({ data: TOKEN } as never);
  mockApi.registerDeviceToken.mockResolvedValue(undefined as never);
});

describe("registration", () => {
  it("registers the token with the API and returns it", async () => {
    granted();

    await expect(registerForPush()).resolves.toBe(TOKEN);
    expect(mockApi.registerDeviceToken).toHaveBeenCalledWith(TOKEN, expect.any(String));
  });

  it("passes the EAS project id, without which Expo cannot mint a token", async () => {
    granted();

    await registerForPush();

    // app.json carries extra.eas.projectId; it is what ties the token to the
    // Expo project whose FCM credentials actually send the notification.
    const projectId = Constants.expoConfig?.extra?.eas?.projectId;
    if (projectId) {
      expect(mockNotifications.getExpoPushTokenAsync).toHaveBeenCalledWith({ projectId });
    } else {
      expect(mockNotifications.getExpoPushTokenAsync).toHaveBeenCalledWith(undefined);
    }
  });

  it("does not re-prompt when permission was already granted", async () => {
    granted();

    await registerForPush();

    expect(mockNotifications.requestPermissionsAsync).not.toHaveBeenCalled();
  });

  it("prompts once when permission has not been asked for yet", async () => {
    undetermined();
    mockNotifications.requestPermissionsAsync.mockResolvedValue({
      status: "granted",
    } as never);

    await expect(registerForPush()).resolves.toBe(TOKEN);
    expect(mockNotifications.requestPermissionsAsync).toHaveBeenCalledTimes(1);
  });
});

describe("the quiet paths", () => {
  it("returns null on a simulator instead of trying to register", async () => {
    (Device as { isDevice: boolean }).isDevice = false;
    granted();

    await expect(registerForPush()).resolves.toBeNull();
    expect(mockApi.registerDeviceToken).not.toHaveBeenCalled();
  });

  it("returns null when the user refuses the prompt", async () => {
    undetermined();
    mockNotifications.requestPermissionsAsync.mockResolvedValue({
      status: "denied",
    } as never);

    await expect(registerForPush()).resolves.toBeNull();
    expect(mockNotifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
  });

  it("swallows a token service outage rather than failing the login it runs during", async () => {
    granted();
    mockNotifications.getExpoPushTokenAsync.mockRejectedValue(
      new Error("Failed to get push token for device")
    );

    // This is the whole point of the try/catch in registerForPush. Without it,
    // an Expo push outage locks every user out of the app.
    await expect(registerForPush()).resolves.toBeNull();
  });

  it("swallows an API failure while registering the token", async () => {
    granted();
    mockApi.registerDeviceToken.mockRejectedValue(new Error("500"));

    await expect(registerForPush()).resolves.toBeNull();
  });
});

describe("unregistration", () => {
  it("surrenders the token that was actually registered", async () => {
    granted();
    await registerForPush();
    expect(currentPushToken()).toBe(TOKEN);

    await unregisterPush();

    // Releasing the exact token matters on a shared device: the next person to
    // sign in here must not receive the previous user's job alerts.
    expect(mockApi.unregisterDeviceToken).toHaveBeenCalledWith(TOKEN);
    expect(currentPushToken()).toBeNull();
  });

  it("does nothing when no token was ever registered", async () => {
    await unregisterPush();

    expect(mockApi.unregisterDeviceToken).not.toHaveBeenCalled();
  });

  it("clears the cached token even when the API call fails", async () => {
    granted();
    await registerForPush();
    mockApi.unregisterDeviceToken.mockRejectedValue(new Error("offline"));

    await expect(unregisterPush()).resolves.toBeUndefined();

    // Logging out must complete offline. The server also re-points a token when
    // someone else registers it, so a stale row is self-correcting.
    expect(currentPushToken()).toBeNull();
  });
});

describe("the emulator escape hatch", () => {
  /**
   * An Android emulator on a Play services image can register with FCM and
   * receive normally, so this guard is the only thing between a developer with
   * no test handset and verifying the whole chain. It has two locks because a
   * release build registering emulator tokens would be a real defect.
   */
  const ORIGINAL = process.env.EXPO_PUBLIC_ALLOW_EMULATOR_PUSH;

  afterEach(() => {
    process.env.EXPO_PUBLIC_ALLOW_EMULATOR_PUSH = ORIGINAL;
    (globalThis as { __DEV__?: boolean }).__DEV__ = true;
  });

  it("registers on an emulator when explicitly opted in during development", async () => {
    (globalThis as { __DEV__?: boolean }).__DEV__ = true;
    process.env.EXPO_PUBLIC_ALLOW_EMULATOR_PUSH = "1";
    (Device as { isDevice: boolean }).isDevice = false;
    granted();

    await expect(registerForPush()).resolves.toBe(TOKEN);
  });

  it("stays closed on an emulator without the opt-in", async () => {
    (globalThis as { __DEV__?: boolean }).__DEV__ = true;
    delete process.env.EXPO_PUBLIC_ALLOW_EMULATOR_PUSH;
    (Device as { isDevice: boolean }).isDevice = false;
    granted();

    // Default behaviour must be unchanged: opting in has to be deliberate.
    await expect(registerForPush()).resolves.toBeNull();
  });

  it("stays closed in a release build even when the variable is set", async () => {
    (globalThis as { __DEV__?: boolean }).__DEV__ = false;
    process.env.EXPO_PUBLIC_ALLOW_EMULATOR_PUSH = "1";
    (Device as { isDevice: boolean }).isDevice = false;
    granted();

    // The lock that actually matters. A shipped build must never register a
    // token for a device that cannot receive anything.
    await expect(registerForPush()).resolves.toBeNull();
  });
});
