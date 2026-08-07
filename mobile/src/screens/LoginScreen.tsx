import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useState } from "react";
import { Text, View } from "react-native";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { RootStackParamList } from "../navigation";
import { Button, ErrorText, Input, Screen, colors } from "../ui";

type Props = NativeStackScreenProps<RootStackParamList, "Login">;

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
      <View style={{ marginTop: 48, marginBottom: 12 }}>
        <Text style={{ fontSize: 34, fontWeight: "800", color: colors.text }}>ToolBelt</Text>
        <Text style={{ fontSize: 16, color: colors.subtext }}>
          Skilled help, around the corner.
        </Text>
      </View>
      <Input
        label="Email"
        value={email}
        onChangeText={setEmail}
        keyboardType="email-address"
        placeholder="you@example.com"
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
    </Screen>
  );
}
