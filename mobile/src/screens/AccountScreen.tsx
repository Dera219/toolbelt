import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import React, { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { BillingProfile, PayoutAccount } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import type { RootStackParamList } from "../navigation";
import { Badge, Button, Card, ErrorText, Row, Screen, Subtext, Title } from "../ui";

export default function AccountScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { user, mode, canWork, setMode, logout } = useAuth();
  const [billing, setBilling] = useState<BillingProfile | null>(null);
  const [payout, setPayout] = useState<PayoutAccount | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.getPayoutAccount().then(setPayout).catch(() => setPayout(null));
  }, []);

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
    // Dev flow: the fake provider accepts any ref. In production this button opens
    // the Stripe PaymentSheet and passes the tokenized payment method ref instead.
    setBilling(await api.setPaymentMethod("pm_card_dev"));
    setNotice("Payment method saved");
  });

  const setUpPayouts = run(async () => {
    const account = await api.createPayoutAccount();
    setPayout(account);
    setNotice(
      account.payouts_enabled
        ? "Payouts are active"
        : "Payout account created — finish onboarding to receive payouts"
    );
  });

  if (!user) return null;

  return (
    <Screen>
      <Card>
        <Title>{user.full_name}</Title>
        <Subtext>{user.email}</Subtext>
        <Row>
          <Badge label={user.role} />
          {user.phone_verified && <Badge label="verified" />}
        </Row>
      </Card>

      {canWork && (
        <Card>
          <Title>Mode</Title>
          <Row>
            <Button
              label={mode === "customer" ? "✓ Hiring" : "Hiring"}
              variant={mode === "customer" ? "primary" : "secondary"}
              onPress={() => setMode("customer")}
            />
            <Button
              label={mode === "worker" ? "✓ Working" : "Working"}
              variant={mode === "worker" ? "primary" : "secondary"}
              onPress={() => setMode("worker")}
            />
          </Row>
        </Card>
      )}

      <Card>
        <Title>Payment method</Title>
        <Subtext>
          {billing?.default_payment_method_ref
            ? "Card on file — you can book workers."
            : "Add a card to book workers."}
        </Subtext>
        <Button label="Add / replace card" onPress={addPaymentMethod} loading={busy} />
      </Card>

      {canWork && (
        <>
          <Card>
            <Title>Getting paid</Title>
            {payout ? (
              <Row>
                <Badge label={payout.payouts_enabled ? "paid_out" : "pending"} />
                <Subtext>
                  {payout.payouts_enabled ? "Payouts active" : "Onboarding incomplete"}
                </Subtext>
              </Row>
            ) : (
              <Subtext>Set up your payout account to receive earnings.</Subtext>
            )}
            <Button label="Set up payouts" onPress={setUpPayouts} loading={busy} />
          </Card>
          <Button
            label="Worker profile & vetting"
            variant="secondary"
            onPress={() => navigation.navigate("WorkerProfileEdit")}
          />
        </>
      )}

      <ErrorText message={error} />
      {notice && <Subtext>{notice}</Subtext>}
      <Button label="Log out" variant="danger" onPress={() => logout()} />
    </Screen>
  );
}
