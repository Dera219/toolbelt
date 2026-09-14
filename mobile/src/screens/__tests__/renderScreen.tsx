/**
 * Shared setup for screen tests.
 *
 * Every screen renders through `ui.Screen`, which calls `useSafeAreaInsets()`
 * and throws without a provider above it. Wrapping each test file separately
 * would mean each one could forget, and a screen test that fails to mount looks
 * identical to a screen that is broken.
 *
 * Metrics are fixed rather than device-derived so a layout assertion cannot pass
 * on one simulated device and fail on another.
 *
 * Importing this module also does the two things below. Between them they are
 * why the suite stopped failing a handful of screen tests on a busy machine and
 * none on the next run.
 */

import { render, type RenderOptions } from "@testing-library/react-native";
import React, { type ReactElement } from "react";
import {
  ActivityIndicator,
  Animated,
  Easing,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

// Evaluate React Native's component modules now, while this file is imported,
// rather than inside a test.
//
// `react-native` exports each component through a lazy getter, so its module
// tree is evaluated the first time anything reads it — and every test file gets
// a fresh module registry. Left alone, that happens inside the first `render`
// of each file, and whichever test renders first pays for it: 1.0–2.7 s on an
// idle M3 efficiency core, against ~25 ms for the next test's render. Under
// load that one test went past Jest's timeout while the rest of its file
// passed, which is the entire pattern behind "6 failed" followed by a clean run.
//
// Jest does not time module evaluation, so reading the getters here takes the
// cost out of every test's budget. The list is every value the app imports from
// `react-native`; one missing from it is not a failure, only a slower first
// test. Do not replace it with a loop over every export: several of React
// Native's getters throw for removed APIs or warn for deprecated ones.
void [
  ActivityIndicator,
  Animated,
  Easing,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
];

// A screen test is CPU-bound from mount to last assertion, so its wall time
// scales with how busy the machine is, and Jest's 5 s default was never
// calibrated for that. With the preload above, the slowest screen test still
// reached 2.7 s on efficiency cores alone, which is too little margin for a
// laptop that has just run the API suite with a browser open.
//
// This loosens no assertion. `findBy*` and `waitFor` keep RNTL's own 1 s limit,
// so a screen that never reaches the expected state still fails in about a
// second; this only stops a slow machine from being reported as a broken
// screen. It applies to screen test files alone, because only they import this.
jest.setTimeout(20_000);

const METRICS = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};

/**
 * Note the `await` this forces on callers: RNTL 14 made `render` **async**.
 * Forgetting it fails in a thoroughly misleading way — `screen` reports
 * "`render` function has not been called", the returned value is a Promise
 * whose query methods are all undefined, and nothing mentions a promise.
 */
export function renderScreen(ui: ReactElement, options?: RenderOptions) {
  return render(ui, {
    wrapper: ({ children }) => (
      <SafeAreaProvider initialMetrics={METRICS}>{children}</SafeAreaProvider>
    ),
    ...options,
  });
}

// Deliberately NOT `export * from "@testing-library/react-native"`. RNTL's
// `screen` is a binding it replaces on each render, and Babel's CJS interop
// copies re-exported values at module init — so a re-exported `screen` is
// frozen as the "render has not been called" placeholder forever. Import it
// from the library directly.
