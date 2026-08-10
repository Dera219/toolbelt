import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { LinearGradient } from "expo-linear-gradient";
import React, { useState } from "react";
import { Text, View } from "react-native";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { RootStackParamList } from "../navigation";
import { palette, space } from "../theme";
import { Body, Button, ErrorText, FadeIn, Input, Row, Screen, space as gap } from "../ui";

type Props = NativeStackScreenProps<RootStackParamList, "Login">;

const PROOF = [
  { icon: "🛡️", label: "Vetted pros" },
  { icon: "🔒", label: "Protected pay" },
  { icon: "⭐", label: "Real reviews" },
];

export default function LoginScreen({ navigation }: Props) {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await login(email.trim(), password);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <FadeIn>
        <LinearGradient
          colors={[palette.navy, "#2b3d5f"]}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={{ borderRadius: 22, padding: space.xl, gap: 10, marginTop: space.lg }}
        >
          <View
            style={{
              width: 46,
              height: 46,
              borderRadius: 13,
              backgroundColor: palette.amber,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Text style={{ fontSize: 24 }}>🛠️</Text>
          </View>
          <Text style={{ fontSize: 34, fontWeight: "800", color: "#fff", letterSpacing: -0.8 }}>
            ToolBelt
          </Text>
          <Text style={{ fontSize: 16, color: "rgba(255,255,255,0.78)", lineHeight: 22 }}>
            Skilled help, around the corner. Post a job, get offers in minutes.
          </Text>
          <Row gap={gap.sm} style={{ marginTop: space.sm }}>
            {PROOF.map((p) => (
              <View
                key={p.label}
                style={{
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 5,
                  backgroundColor: "rgba(255,255,255,0.12)",
                  borderRadius: 999,
                  paddingHorizontal: 10,
                  paddingVertical: 5,
                }}
              >
                <Text style={{ fontSize: 12 }}>{p.icon}</Text>
                <Text style={{ fontSize: 12, fontWeight: "600", color: "#fff" }}>{p.label}</Text>
              </View>
            ))}
          </Row>
        </LinearGradient>
      </FadeIn>

      <FadeIn delay={90}>
        <View style={{ gap: space.md, marginTop: space.md }}>
          <Input
            label="Email"
            value={email}
            onChangeText={setEmail}
            keyboardType="email-address"
            placeholder="you@example.com"
            autoComplete="email"
          />
          <Input
            label="Password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            placeholder="••••••••••"
          />
          <ErrorText message={error} />
          <Button label="Log in" onPress={submit} loading={busy} />
          <Button
            label="Create an account"
            variant="secondary"
            onPress={() => navigation.navigate("Register")}
          />
          <Body>New here? Signing up takes about a minute.</Body>
        </View>
      </FadeIn>
    </Screen>
  );
}
