import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { LinearGradient } from "expo-linear-gradient";
import React, { useCallback, useState } from "react";
import { Text, View } from "react-native";
import { ApiError, api, money } from "../api/client";
import type { Balance, BillingProfile, PayoutAccount } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import type { RootStackParamList } from "../navigation";
import { palette, radius, space } from "../theme";
import {
  Badge,
  Body,
  Button,
  Caption,
  Card,
  Choice,
  ErrorText,
  FadeIn,
  NoticeText,
  Price,
  Row,
  Screen,
  SectionHeader,
  Subtext,
  Title,
} from "../ui";

export default function AccountScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { user, mode, canWork, setMode, logout } = useAuth();
  const [billing, setBilling] = useState<BillingProfile | null>(null);
  const [payout, setPayout] = useState<PayoutAccount | null>(null);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useFocusEffect(
    useCallback(() => {
      api.getPayoutAccount().then(setPayout).catch(() => setPayout(null));
      if (canWork) api.balance().then(setBalance).catch(() => setBalance(null));
    }, [canWork])
  );

  const run = (fn: () => Promise<void>) => async () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const addPaymentMethod = run(async () => {
    // Dev flow: "pm_card_visa" is a real Stripe test-mode PaymentMethod, so this
    // exercises the true attach path. Production opens the Stripe PaymentSheet
    // and passes the tokenized method it returns instead.
    setBilling(await api.setPaymentMethod("pm_card_visa"));
    setNotice("Test card saved");
  });

  const setUpPayouts = run(async () => {
    const account = await api.createPayoutAccount();
    setPayout(account);
    setNotice(
      account.payouts_enabled
        ? "Payouts are active"
        : "Payout account created — finish onboarding to get paid"
    );
  });

  if (!user) return null;

  const initials = user.full_name
    .split(" ")
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <Screen>
      <FadeIn>
        <LinearGradient
          colors={[palette.navy, "#2b3d5f"]}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={{ borderRadius: radius.xl, padding: space.xl, gap: space.md }}
        >
          <Row gap={space.md}>
            <View style={styles.avatar}>
              <Text style={{ fontSize: 20, fontWeight: "800", color: palette.navy }}>
                {initials}
              </Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 20, fontWeight: "800", color: "#fff" }}>
                {user.full_name}
              </Text>
              <Text style={{ fontSize: 14, color: "rgba(255,255,255,0.72)" }}>{user.email}</Text>
            </View>
          </Row>
          <Row gap={space.sm}>
            <Badge label={user.role} tone="warning" />
            <Badge
              label={user.phone_verified ? "phone verified" : "phone unverified"}
              tone={user.phone_verified ? "positive" : "neutral"}
            />
          </Row>
        </LinearGradient>
      </FadeIn>

      {canWork && balance != null && (
        <FadeIn delay={60}>
          <Card raised>
            <Caption>PENDING BALANCE</Caption>
            <Price value={money(balance.balance_cents, balance.currency)} size="lg" />
            <Subtext>
              {balance.balance_cents > 0
                ? "Earned and waiting to be paid out."
                : "Finish a job and your earnings land here."}
            </Subtext>
          </Card>
        </FadeIn>
      )}

      {canWork && (
        <FadeIn delay={90}>
          <Card>
            <Title>I'm here to…</Title>
            <Subtext>Switch any time — one account, both sides.</Subtext>
            <Choice
              value={mode}
              onChange={(next) => setMode(next)}
              options={[
                { value: "customer", label: "Hire help", icon: "🧾" },
                { value: "worker", label: "Find work", icon: "🔧" },
              ]}
            />
          </Card>
        </FadeIn>
      )}

      <SectionHeader title="Payments" />

      <Card>
        <Row between>
          <Title>Payment method</Title>
          {billing?.default_payment_method_ref ? <Badge label="ready" tone="positive" /> : null}
        </Row>
        <Body>
          {billing?.default_payment_method_ref
            ? "Card on file. You're set to book pros."
            : "Add a card so you can book a pro when you accept an offer."}
        </Body>
        <Button
          label={billing?.default_payment_method_ref ? "Replace card" : "Add card"}
          icon="💳"
          variant={billing?.default_payment_method_ref ? "secondary" : "primary"}
          onPress={addPaymentMethod}
          loading={busy}
        />
      </Card>

      {canWork && (
        <>
          <Card>
            <Row between>
              <Title>Getting paid</Title>
              {payout ? (
                <Badge
                  label={payout.payouts_enabled ? "active" : "incomplete"}
                  tone={payout.payouts_enabled ? "positive" : "warning"}
                />
              ) : null}
            </Row>
            <Body>
              {payout?.payouts_enabled
                ? "Payouts are active — earnings transfer automatically after each job."
                : "Connect your bank through Stripe to receive earnings."}
            </Body>
            {!payout?.payouts_enabled && (
              <Button
                label={payout ? "Finish onboarding" : "Set up payouts"}
                icon="🏦"
                variant="accent"
                onPress={setUpPayouts}
                loading={busy}
              />
            )}
          </Card>

          <SectionHeader title="Work" />
          <Card onPress={() => navigation.navigate("WorkerProfileEdit")}>
            <Row between>
              <View>
                <Title>Worker profile & vetting</Title>
                <Subtext>Trade, rate, service area, verification</Subtext>
              </View>
              <Text style={{ fontSize: 22, color: palette.ink3 }}>›</Text>
            </Row>
          </Card>
        </>
      )}

      <ErrorText message={error} />
      <NoticeText message={notice} />
      <View style={{ marginTop: space.sm }}>
        <Button label="Log out" variant="danger" onPress={() => logout()} />
      </View>
    </Screen>
  );
}

const styles = {
  avatar: {
    width: 52,
    height: 52,
    borderRadius: radius.pill,
    backgroundColor: palette.amber,
    alignItems: "center" as const,
    justifyContent: "center" as const,
  },
};
