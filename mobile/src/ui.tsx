import React from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type TextInputProps,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

export const colors = {
  bg: "#f5f6f8",
  card: "#ffffff",
  text: "#16181d",
  subtext: "#5c6470",
  primary: "#1f6feb",
  danger: "#c93b3b",
  success: "#1a7f4b",
  warning: "#b7791f",
  border: "#e2e5ea",
};

export const statusColors: Record<string, string> = {
  open: colors.primary,
  assigned: colors.warning,
  in_progress: colors.warning,
  completed: colors.success,
  cancelled: colors.subtext,
  disputed: colors.danger,
  pending: colors.warning,
  accepted: colors.success,
  expired: colors.subtext,
  verified: colors.success,
  unverified: colors.subtext,
  rejected: colors.danger,
  authorized: colors.warning,
  captured: colors.warning,
  paid_out: colors.success,
  released: colors.subtext,
  refunded: colors.danger,
};

export function Screen({ children, scroll = true }: { children: React.ReactNode; scroll?: boolean }) {
  const insets = useSafeAreaInsets();
  const style = [styles.screen, { paddingBottom: insets.bottom + 12 }];
  if (!scroll) return <View style={[style, { flex: 1 }]}>{children}</View>;
  return (
    <ScrollView style={{ backgroundColor: colors.bg }} contentContainerStyle={style}>
      {children}
    </ScrollView>
  );
}

export function Card({ children, onPress }: { children: React.ReactNode; onPress?: () => void }) {
  if (onPress)
    return (
      <Pressable onPress={onPress} style={({ pressed }) => [styles.card, pressed && { opacity: 0.7 }]}>
        {children}
      </Pressable>
    );
  return <View style={styles.card}>{children}</View>;
}

export function Title({ children }: { children: React.ReactNode }) {
  return <Text style={styles.title}>{children}</Text>;
}

export function Subtext({ children }: { children: React.ReactNode }) {
  return <Text style={styles.subtext}>{children}</Text>;
}

export function Badge({ label }: { label: string }) {
  const color = statusColors[label] ?? colors.subtext;
  return (
    <View style={[styles.badge, { borderColor: color }]}>
      <Text style={{ color, fontSize: 12, fontWeight: "600" }}>{label.replace("_", " ")}</Text>
    </View>
  );
}

export function Button({
  label,
  onPress,
  variant = "primary",
  loading = false,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "danger";
  loading?: boolean;
  disabled?: boolean;
}) {
  const bg =
    variant === "primary" ? colors.primary : variant === "danger" ? colors.danger : colors.card;
  const fg = variant === "secondary" ? colors.text : "#fff";
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        styles.button,
        { backgroundColor: bg, borderWidth: variant === "secondary" ? 1 : 0 },
        (pressed || disabled || loading) && { opacity: 0.6 },
      ]}
    >
      {loading ? (
        <ActivityIndicator color={fg} />
      ) : (
        <Text style={{ color: fg, fontWeight: "600", fontSize: 16 }}>{label}</Text>
      )}
    </Pressable>
  );
}

export function Input({
  label,
  ...props
}: TextInputProps & { label: string }) {
  return (
    <View style={{ marginBottom: 12 }}>
      <Text style={styles.inputLabel}>{label}</Text>
      <TextInput
        placeholderTextColor={colors.subtext}
        style={styles.input}
        autoCapitalize="none"
        {...props}
      />
    </View>
  );
}

export function ErrorText({ message }: { message: string | null }) {
  if (!message) return null;
  return <Text style={styles.error}>{message}</Text>;
}

export function Row({ children }: { children: React.ReactNode }) {
  return <View style={styles.row}>{children}</View>;
}

export function Loading() {
  return (
    <View style={{ flex: 1, justifyContent: "center", backgroundColor: colors.bg }}>
      <ActivityIndicator size="large" color={colors.primary} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { padding: 16, backgroundColor: colors.bg, gap: 12 },
  card: {
    backgroundColor: colors.card,
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: colors.border,
    gap: 6,
  },
  title: { fontSize: 17, fontWeight: "700", color: colors.text },
  subtext: { fontSize: 14, color: colors.subtext },
  badge: {
    alignSelf: "flex-start",
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 2,
  },
  button: {
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: "center",
    borderColor: colors.border,
  },
  input: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 12,
    fontSize: 16,
    color: colors.text,
  },
  inputLabel: { fontSize: 13, fontWeight: "600", color: colors.subtext, marginBottom: 4 },
  error: { color: colors.danger, fontSize: 14 },
  row: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
});
