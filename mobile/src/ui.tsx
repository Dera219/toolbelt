import * as Haptics from "expo-haptics";
import { LinearGradient } from "expo-linear-gradient";
import React, { useEffect, useRef } from "react";
import {
  ActivityIndicator,
  Animated,
  Easing,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type RefreshControlProps,
  type TextInputProps,
  type ViewStyle,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  colors,
  elevation,
  palette,
  prettyStatus,
  radius,
  space,
  statusTone,
  toneColors,
  type,
  type Tone,
} from "./theme";

export { colors, palette, radius, space, tradeMeta, type } from "./theme";

const tapFeedback = (style: Haptics.ImpactFeedbackStyle = Haptics.ImpactFeedbackStyle.Light) => {
  if (Platform.OS !== "web") Haptics.impactAsync(style).catch(() => {});
};

export const successFeedback = () => {
  if (Platform.OS !== "web")
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
};

/* ------------------------------------------------------------------ layout */

export function Screen({
  children,
  scroll = true,
  refreshControl,
}: {
  children: React.ReactNode;
  scroll?: boolean;
  refreshControl?: React.ReactElement<RefreshControlProps>;
}) {
  const insets = useSafeAreaInsets();
  const pad = { padding: space.lg, paddingBottom: insets.bottom + space.xl, gap: space.md };
  if (!scroll)
    return <View style={[{ flex: 1, backgroundColor: colors.bg }, pad]}>{children}</View>;
  return (
    <ScrollView
      style={{ backgroundColor: colors.bg }}
      contentContainerStyle={pad}
      refreshControl={refreshControl}
      keyboardShouldPersistTaps="handled"
    >
      {children}
    </ScrollView>
  );
}

export function Row({
  children,
  gap = space.sm,
  between = false,
  style,
}: {
  children: React.ReactNode;
  gap?: number;
  between?: boolean;
  style?: ViewStyle;
}) {
  return (
    <View
      style={[
        {
          flexDirection: "row",
          alignItems: "center",
          flexWrap: "wrap",
          gap,
          justifyContent: between ? "space-between" : "flex-start",
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}

/* -------------------------------------------------------------- animation */

/** Fades and lifts children in — used to stagger lists so they feel alive. */
export function FadeIn({
  children,
  delay = 0,
  style,
}: {
  children: React.ReactNode;
  delay?: number;
  style?: ViewStyle;
}) {
  const progress = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(progress, {
      toValue: 1,
      duration: 320,
      delay,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: Platform.OS !== "web",
    }).start();
  }, [progress, delay]);
  return (
    <Animated.View
      style={[
        {
          opacity: progress,
          transform: [
            { translateY: progress.interpolate({ inputRange: [0, 1], outputRange: [10, 0] }) },
          ],
        },
        style,
      ]}
    >
      {children}
    </Animated.View>
  );
}

/** Pressable that scales down on touch. The single source of tap feel. */
function Tappable({
  children,
  onPress,
  disabled,
  style,
  scaleTo = 0.97,
  haptic = true,
}: {
  children: React.ReactNode;
  onPress?: () => void;
  disabled?: boolean;
  style?: ViewStyle | ViewStyle[];
  scaleTo?: number;
  haptic?: boolean;
}) {
  const scale = useRef(new Animated.Value(1)).current;
  const to = (value: number) =>
    Animated.spring(scale, {
      toValue: value,
      useNativeDriver: Platform.OS !== "web",
      speed: 40,
      bounciness: 4,
    }).start();

  return (
    <Pressable
      onPressIn={() => !disabled && to(scaleTo)}
      onPressOut={() => to(1)}
      onPress={
        onPress
          ? () => {
              if (haptic) tapFeedback();
              onPress();
            }
          : undefined
      }
      disabled={disabled}
    >
      <Animated.View style={[{ transform: [{ scale }] }, style]}>{children}</Animated.View>
    </Pressable>
  );
}

/* ------------------------------------------------------------- containers */

export function Card({
  children,
  onPress,
  padded = true,
  raised = false,
  style,
}: {
  children: React.ReactNode;
  onPress?: () => void;
  padded?: boolean;
  raised?: boolean;
  style?: ViewStyle;
}) {
  const base: ViewStyle[] = [
    styles.card,
    padded ? { padding: space.lg } : {},
    raised ? (elevation.md as ViewStyle) : (elevation.sm as ViewStyle),
    style ?? {},
  ];
  if (onPress) return <Tappable onPress={onPress} style={base} scaleTo={0.985}>{children}</Tappable>;
  return <View style={base}>{children}</View>;
}

/* ------------------------------------------------------------------- text */

export const Display = ({ children }: { children: React.ReactNode }) => (
  <Text style={[type.display, { color: colors.text }]}>{children}</Text>
);
export const H1 = ({ children }: { children: React.ReactNode }) => (
  <Text style={[type.h1, { color: colors.text }]}>{children}</Text>
);
export const Title = ({ children }: { children: React.ReactNode }) => (
  <Text style={[type.h2, { color: colors.text }]}>{children}</Text>
);
export const Body = ({ children }: { children: React.ReactNode }) => (
  <Text style={[type.body, { color: colors.body, lineHeight: 21 }]}>{children}</Text>
);
export const Subtext = ({ children }: { children: React.ReactNode }) => (
  <Text style={[type.body, { color: colors.subtext }]}>{children}</Text>
);
export const Caption = ({ children }: { children: React.ReactNode }) => (
  <Text style={[type.caption, { color: colors.subtext }]}>{children}</Text>
);

export function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <Row between style={{ marginTop: space.sm }}>
      <Text style={[type.label, { color: colors.subtext, textTransform: "uppercase" }]}>
        {title}
      </Text>
      {action}
    </Row>
  );
}

/* ------------------------------------------------------------------ chips */

export function Badge({ label, tone }: { label: string; tone?: Tone }) {
  const resolved = toneColors[tone ?? statusTone[label] ?? "neutral"];
  return (
    <View style={[styles.badge, { backgroundColor: resolved.bg }]}>
      <Text style={[type.caption, { color: resolved.fg }]}>{prettyStatus(label)}</Text>
    </View>
  );
}

export function Pill({ icon, label }: { icon?: string; label: string }) {
  return (
    <View style={styles.pill}>
      {icon ? <Text style={{ fontSize: 13 }}>{icon}</Text> : null}
      <Text style={[type.caption, { color: colors.body }]}>{label}</Text>
    </View>
  );
}

/** Big money figure — prices are the thing people actually scan for. */
export function Price({ value, size = "md" }: { value: string; size?: "md" | "lg" }) {
  return (
    <Text
      style={{
        fontSize: size === "lg" ? 28 : 20,
        fontWeight: "800",
        letterSpacing: -0.6,
        color: colors.text,
      }}
    >
      {value}
    </Text>
  );
}

/* ---------------------------------------------------------------- buttons */

type ButtonVariant = "primary" | "accent" | "secondary" | "ghost" | "danger";

export function Button({
  label,
  onPress,
  variant = "primary",
  loading = false,
  disabled = false,
  icon,
  size = "md",
  full = true,
}: {
  label: string;
  onPress: () => void;
  variant?: ButtonVariant;
  loading?: boolean;
  disabled?: boolean;
  icon?: string;
  size?: "sm" | "md";
  full?: boolean;
}) {
  const inactive = disabled || loading;
  const surface: Record<ButtonVariant, ViewStyle> = {
    primary: { backgroundColor: colors.primary },
    accent: { backgroundColor: palette.amber },
    secondary: { backgroundColor: colors.card, borderWidth: 1.5, borderColor: colors.border },
    ghost: { backgroundColor: "transparent" },
    danger: { backgroundColor: palette.redSoft },
  };
  const fg: Record<ButtonVariant, string> = {
    primary: "#fff",
    accent: palette.ink,
    secondary: colors.text,
    ghost: colors.body,
    danger: palette.red,
  };

  return (
    <Tappable onPress={onPress} disabled={inactive} style={{ alignSelf: full ? "stretch" : "auto" }}>
      <View
        style={[
          styles.button,
          size === "sm" ? { paddingVertical: 9, paddingHorizontal: space.md } : {},
          surface[variant],
          variant !== "ghost" && !inactive ? (elevation.sm as ViewStyle) : {},
          inactive ? { opacity: 0.5 } : {},
        ]}
      >
        {loading ? (
          <ActivityIndicator color={fg[variant]} />
        ) : (
          <Text
            style={{
              color: fg[variant],
              fontWeight: "700",
              fontSize: size === "sm" ? 14 : 16,
              letterSpacing: -0.2,
            }}
          >
            {icon ? `${icon}  ` : ""}
            {label}
          </Text>
        )}
      </View>
    </Tappable>
  );
}

/** Horizontal choice group — replaces stacks of look-alike buttons. */
export function Choice<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string; icon?: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <Row gap={space.sm}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <Tappable key={option.value} onPress={() => onChange(option.value)} scaleTo={0.94}>
            <View style={[styles.choice, active ? styles.choiceActive : {}]}>
              {option.icon ? <Text style={{ fontSize: 15 }}>{option.icon}</Text> : null}
              <Text
                style={[
                  type.caption,
                  { color: active ? "#fff" : colors.body, fontSize: 13.5 },
                ]}
              >
                {option.label}
              </Text>
            </View>
          </Tappable>
        );
      })}
    </Row>
  );
}

/* ----------------------------------------------------------------- inputs */

export function Input({
  label,
  hint,
  prefix,
  ...props
}: TextInputProps & { label: string; hint?: string; prefix?: string }) {
  const [focused, setFocused] = React.useState(false);
  return (
    <View style={{ gap: 6 }}>
      <Text style={[type.label, { color: colors.body }]}>{label}</Text>
      <View style={[styles.inputWrap, focused ? styles.inputWrapFocused : {}]}>
        {prefix ? (
          <Text style={[type.body, { color: colors.subtext, marginRight: 2 }]}>{prefix}</Text>
        ) : null}
        <TextInput
          placeholderTextColor={palette.ink3}
          style={styles.input}
          autoCapitalize="none"
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          {...props}
        />
      </View>
      {hint ? <Caption>{hint}</Caption> : null}
    </View>
  );
}

export function ErrorText({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <View style={styles.errorBox}>
      <Text style={[type.body, { color: palette.red, flex: 1 }]}>{message}</Text>
    </View>
  );
}

export function NoticeText({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <View style={[styles.errorBox, { backgroundColor: palette.greenSoft }]}>
      <Text style={[type.body, { color: palette.green, flex: 1 }]}>{message}</Text>
    </View>
  );
}

/* ------------------------------------------------------------ empty/load */

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon: string;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <FadeIn>
      <View style={[styles.card, elevation.sm as ViewStyle, { padding: space.xl, gap: space.sm }]}>
        <View style={styles.emptyIcon}>
          <Text style={{ fontSize: 26 }}>{icon}</Text>
        </View>
        <Title>{title}</Title>
        <Body>{body}</Body>
        {action ? <View style={{ marginTop: space.sm }}>{action}</View> : null}
      </View>
    </FadeIn>
  );
}

/** Shimmering placeholder — makes loading feel instant instead of blank. */
export function Skeleton({ height = 84 }: { height?: number }) {
  const shimmer = useRef(new Animated.Value(0.4)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(shimmer, {
          toValue: 1,
          duration: 700,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: Platform.OS !== "web",
        }),
        Animated.timing(shimmer, {
          toValue: 0.4,
          duration: 700,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: Platform.OS !== "web",
        }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [shimmer]);
  return (
    <Animated.View
      style={{
        height,
        borderRadius: radius.lg,
        backgroundColor: palette.line2,
        opacity: shimmer,
      }}
    />
  );
}

export function Loading() {
  return (
    <View style={{ flex: 1, justifyContent: "center", backgroundColor: colors.bg }}>
      <ActivityIndicator size="large" color={colors.primary} />
    </View>
  );
}

/* ----------------------------------------------------------------- hero */

export function Hero({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <LinearGradient
      colors={[palette.navy, "#2b3d5f"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.hero}
    >
      <Text style={[type.h1, { color: "#fff" }]}>{title}</Text>
      {subtitle ? (
        <Text style={[type.body, { color: "rgba(255,255,255,0.75)" }]}>{subtitle}</Text>
      ) : null}
      {children}
    </LinearGradient>
  );
}

/** Job status as a 4-step progress rail — turns state into something visual. */
const JOB_STEPS = ["open", "assigned", "in_progress", "completed"] as const;

export function StatusRail({ status }: { status: string }) {
  const index = JOB_STEPS.indexOf(status as (typeof JOB_STEPS)[number]);
  if (index < 0) return null; // cancelled / disputed use a badge instead
  const labels = ["Posted", "Booked", "In progress", "Done"];
  return (
    <Row gap={0} style={{ marginVertical: space.xs }}>
      {JOB_STEPS.map((step, i) => {
        const done = i <= index;
        return (
          <View key={step} style={{ flex: 1, gap: 6 }}>
            <View style={{ flexDirection: "row", alignItems: "center" }}>
              <View style={[styles.dot, done ? styles.dotDone : {}]} />
              {i < JOB_STEPS.length - 1 && (
                <View style={[styles.rail, i < index ? styles.railDone : {}]} />
              )}
            </View>
            <Text
              style={[
                type.caption,
                { color: done ? colors.text : colors.subtext, fontSize: 11 },
              ]}
            >
              {labels[i]}
            </Text>
          </View>
        );
      })}
    </Row>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    gap: space.sm,
  },
  badge: {
    alignSelf: "flex-start",
    borderRadius: radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: palette.line2,
    borderRadius: radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  button: {
    borderRadius: radius.md,
    paddingVertical: 14,
    paddingHorizontal: space.lg,
    alignItems: "center",
    justifyContent: "center",
  },
  choice: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: radius.pill,
    backgroundColor: colors.card,
    borderWidth: 1.5,
    borderColor: colors.border,
  },
  choiceActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  inputWrap: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: 14,
  },
  inputWrapFocused: { borderColor: palette.amber, backgroundColor: "#fff" },
  input: {
    flex: 1,
    paddingVertical: 13,
    fontSize: 16,
    color: colors.text,
    ...(Platform.OS === "web" ? { outlineStyle: "none" as never } : {}),
  },
  errorBox: {
    flexDirection: "row",
    gap: space.sm,
    backgroundColor: palette.redSoft,
    borderRadius: radius.md,
    padding: space.md,
  },
  emptyIcon: {
    width: 52,
    height: 52,
    borderRadius: radius.md,
    backgroundColor: palette.amberSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  hero: {
    borderRadius: radius.xl,
    padding: space.xl,
    gap: 6,
  },
  dot: {
    width: 11,
    height: 11,
    borderRadius: radius.pill,
    backgroundColor: palette.line,
  },
  dotDone: { backgroundColor: palette.amber },
  rail: { flex: 1, height: 3, backgroundColor: palette.line },
  railDone: { backgroundColor: palette.amber },
});
