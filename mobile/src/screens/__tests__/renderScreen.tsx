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
 */

import { render, type RenderOptions } from "@testing-library/react-native";
import React, { type ReactElement } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

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
