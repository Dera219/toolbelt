import { Platform } from "react-native";

/**
 * Design tokens. Direction: a trades brand, not a generic SaaS blue — deep
 * slate ink with a hi-vis amber accent (the colour of work gloves and safety
 * vests), warm neutrals, and confident type. Everything else derives from here.
 */

export const palette = {
  ink: "#12151c",
  ink2: "#3d4552",
  ink3: "#6b7480",
  line: "#e6e8ec",
  line2: "#f0f2f5",
  surface: "#ffffff",
  canvas: "#f7f8fa",

  // Brand
  amber: "#f0a202",
  amberDeep: "#c97f00",
  amberSoft: "#fff4dc",
  navy: "#16233a",
  navySoft: "#e8edf6",

  // Semantic
  green: "#12805c",
  greenSoft: "#e2f5ee",
  red: "#c8342f",
  redSoft: "#fdecec",
  blue: "#2563c9",
  blueSoft: "#e8f0fd",
  slateSoft: "#eef0f4",
};

export const colors = {
  bg: palette.canvas,
  card: palette.surface,
  text: palette.ink,
  subtext: palette.ink3,
  body: palette.ink2,
  primary: palette.navy,
  accent: palette.amber,
  danger: palette.red,
  success: palette.green,
  warning: palette.amberDeep,
  border: palette.line,
};

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };
export const radius = { sm: 8, md: 12, lg: 16, xl: 22, pill: 999 };

export const type = {
  display: { fontSize: 34, fontWeight: "800" as const, letterSpacing: -0.8 },
  h1: { fontSize: 24, fontWeight: "800" as const, letterSpacing: -0.5 },
  h2: { fontSize: 19, fontWeight: "700" as const, letterSpacing: -0.3 },
  body: { fontSize: 15, fontWeight: "500" as const },
  label: { fontSize: 13, fontWeight: "700" as const, letterSpacing: 0.2 },
  caption: { fontSize: 12, fontWeight: "600" as const },
  mono: {
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "ui-monospace" }),
  },
};

/** Soft, layered elevation — shadows read as depth, not as a drop-shadow effect. */
export const elevation = {
  none: {},
  sm: Platform.select({
    ios: {
      shadowColor: palette.ink,
      shadowOpacity: 0.06,
      shadowRadius: 8,
      shadowOffset: { width: 0, height: 2 },
    },
    android: { elevation: 2 },
    default: { boxShadow: "0 2px 8px rgba(18,21,28,0.06)" },
  }),
  md: Platform.select({
    ios: {
      shadowColor: palette.ink,
      shadowOpacity: 0.1,
      shadowRadius: 18,
      shadowOffset: { width: 0, height: 8 },
    },
    android: { elevation: 6 },
    default: { boxShadow: "0 8px 20px rgba(18,21,28,0.10)" },
  }),
} as const;

/** Status → tone. One mapping so every chip in the app agrees. */
export type Tone = "neutral" | "info" | "positive" | "warning" | "critical" | "brand";

export const toneColors: Record<Tone, { fg: string; bg: string }> = {
  neutral: { fg: palette.ink2, bg: palette.slateSoft },
  info: { fg: palette.blue, bg: palette.blueSoft },
  positive: { fg: palette.green, bg: palette.greenSoft },
  warning: { fg: palette.amberDeep, bg: palette.amberSoft },
  critical: { fg: palette.red, bg: palette.redSoft },
  brand: { fg: palette.navy, bg: palette.navySoft },
};

export const statusTone: Record<string, Tone> = {
  // job lifecycle
  open: "info",
  assigned: "brand",
  in_progress: "warning",
  completed: "positive",
  cancelled: "neutral",
  disputed: "critical",
  // offers
  pending: "warning",
  accepted: "positive",
  declined: "neutral",
  withdrawn: "neutral",
  expired: "neutral",
  // vetting
  unverified: "neutral",
  verified: "positive",
  rejected: "critical",
  // payments
  authorized: "warning",
  captured: "info",
  paid_out: "positive",
  released: "neutral",
  refunded: "critical",
};

export const TRADE_META: Record<string, { icon: string; label: string }> = {
  cleaning: { icon: "🧼", label: "Cleaning" },
  moving: { icon: "📦", label: "Moving" },
  handyman: { icon: "🔧", label: "Handyman" },
  assembly: { icon: "🪛", label: "Assembly" },
  yard_work: { icon: "🌿", label: "Yard work" },
  painting: { icon: "🎨", label: "Painting" },
  electrical: { icon: "⚡", label: "Electrical" },
  plumbing: { icon: "🚿", label: "Plumbing" },
  hvac: { icon: "🌡️", label: "HVAC" },
};

export const tradeMeta = (trade: string) =>
  TRADE_META[trade] ?? { icon: "🛠️", label: trade.replace(/_/g, " ") };

export const prettyStatus = (s: string) => s.replace(/_/g, " ");
