import React, { useState } from "react";
import { View } from "react-native";
import type { Role } from "../api/types";
import { space } from "../theme";
import { Button, Caption, ErrorText, Row } from "../ui";
import { useAuth } from "./AuthContext";
import { PROVIDER_LABEL, type SocialProvider } from "./social";

const ICON: Record<SocialProvider, string> = {
  google: "\u{1F310}",
  apple: "\u{1F34E}",
  microsoft: "\u{1FA9F}",
};

/** Renders only the providers the server actually has configured. */
export default function SocialButtons({ role }: { role?: Role }) {
  const { signInWithSocial, socialProviders } = useAuth();
  const [busy, setBusy] = useState<SocialProvider | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (socialProviders.length === 0) return null;

  const run = (provider: SocialProvider) => async () => {
    setError(null);
    setBusy(provider);
    try {
      await signInWithSocial(provider, role);
    } catch (e) {
      setError(e instanceof Error ? e.message : `Could not sign in with ${PROVIDER_LABEL[provider]}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={{ gap: space.sm }}>
      <Row>
        <Caption>Or continue with</Caption>
      </Row>
      {socialProviders.map((provider) => (
        <Button
          key={provider}
          label={`Continue with ${PROVIDER_LABEL[provider]}`}
          icon={ICON[provider]}
          variant="secondary"
          loading={busy === provider}
          disabled={busy !== null && busy !== provider}
          onPress={run(provider)}
        />
      ))}
      <ErrorText message={error} />
    </View>
  );
}
