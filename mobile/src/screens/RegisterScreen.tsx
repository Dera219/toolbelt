import React, { useState } from "react";
import { View } from "react-native";
import { ApiError } from "../api/client";
import type { Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import SocialButtons from "../auth/SocialButtons";
import { space } from "../theme";
import {
  Body,
  Button,
  Caption,
  Card,
  Choice,
  ErrorText,
  FadeIn,
  Input,
  Screen,
  Title,
} from "../ui";

const ROLE_BLURB: Record<Role, string> = {
  customer: "Post jobs and hire vetted pros near you.",
  worker: "Find work nearby and get paid through the app.",
  both: "Hire when you need help, work when you're free.",
};

export default function RegisterScreen() {
  const { register } = useAuth();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("customer");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await register(email.trim(), password, fullName.trim(), role);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Registration failed");
      setBusy(false);
    }
  };

  const ready = fullName.trim() && email.trim() && password.length >= 10;

  return (
    <Screen>
      <FadeIn>
        <Card raised>
          <Title>I want to…</Title>
          <Choice
            value={role}
            onChange={setRole}
            options={[
              { value: "customer", label: "Hire help", icon: "🧾" },
              { value: "worker", label: "Find work", icon: "🔧" },
              { value: "both", label: "Both", icon: "🔁" },
            ]}
          />
          <Body>{ROLE_BLURB[role]}</Body>
        </Card>
      </FadeIn>

      <FadeIn delay={60}>
        <View style={{ gap: space.md }}>
          <Input
            label="Full name"
            value={fullName}
            onChangeText={setFullName}
            placeholder="Chidera Onyebu"
            autoCapitalize="words"
          />
          <Input
            label="Email"
            value={email}
            onChangeText={setEmail}
            placeholder="you@example.com"
            keyboardType="email-address"
          />
          <Input
            label="Password"
            hint="At least 10 characters."
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            placeholder="••••••••••"
          />
          <ErrorText message={error} />
          <Button label="Create account" onPress={submit} loading={busy} disabled={!ready} />
          <SocialButtons role={role} />
          <Caption>You can switch between hiring and working at any time.</Caption>
        </View>
      </FadeIn>
    </Screen>
  );
}
