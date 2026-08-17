/**
 * Babel config.
 *
 * Metro does not need this file — Expo resolves the preset internally when no
 * config is present. Jest does, and only for some of its projects, which is why
 * its absence looked like a bug in jest-expo rather than a missing file here.
 *
 * `jest-expo`'s root preset passes babel-jest a fully resolved options object
 * (see jest-expo/src/resolveBabelOptions.js) that falls back to Expo's internal
 * preset. Its per-platform presets, used by `jest-expo/universal`, replace those
 * options with only a `caller` and drop the presets entirely — so babel-jest
 * runs with no preset at all and dies on the first Flow annotation it meets,
 * inside @react-native/jest-preset's own setup file. A real config file is
 * found by babel's normal lookup regardless of what jest-expo passes, which
 * fixes every project at once.
 *
 * SDK 57 ships the preset inside the `expo` package rather than as a separate
 * `babel-preset-expo` dependency, which is not installed here — hence the
 * internal path.
 */

module.exports = function (api) {
  api.cache(true);
  return {
    presets: ["expo/internal/babel-preset"],
  };
};
