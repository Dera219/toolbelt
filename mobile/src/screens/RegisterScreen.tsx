import React, { useState } from "react";
import { ApiError } from "../api/client";
import type { Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, ErrorText, Input, Row, Screen, Subtext, Title } from "../ui";

const ROLES: { value: Role; label: string }[] = [
  { value: "customer", label: "Hire help" },
  { value: "worker", label: "Find work" },
  { value: "both", label: "Both" },
];

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

  return (
    <Screen>
      <Title>Create your account</Title>
      <Input label="Full name" value={fullName} onChangeText={setFullName} autoCapitalize="words" />
      <Input
        label="Email"
        value={email}
        onChangeText={setEmail}
        keyboardType="email-address"
        placeholder="you@example.com"
      />
      <Input
        label="Password (10+ characters)"
        value={password}
        onChangeText={setPassword}
        secureTextEntry
      />
      <Subtext>I want to…</Subtext>
      <Row>
        {ROLES.map((r) => (
          <Button
            key={r.value}
            label={role === r.value ? `✓ ${r.label}` : r.label}
            variant={role === r.value ? "primary" : "secondary"}
            onPress={() => setRole(r.value)}
          />
        ))}
      </Row>
      <ErrorText message={error} />
      <Button label="Sign up" onPress={submit} loading={busy} />
    </Screen>
  );
}
