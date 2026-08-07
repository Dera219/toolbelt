import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import * as Location from "expo-location";
import React, { useState } from "react";
import { Switch } from "react-native";
import { ApiError, api } from "../../api/client";
import { TRADES } from "../../config";
import type { RootStackParamList } from "../../navigation";
import { Button, ErrorText, Input, Row, Screen, Subtext, Title } from "../../ui";

export default function PostJobScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [trade, setTrade] = useState<string>("cleaning");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [address, setAddress] = useState("");
  const [budget, setBudget] = useState("");
  const [supplies, setSupplies] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") throw new ApiError(0, "Location permission is required");
      const pos = await Location.getCurrentPositionAsync({});
      const budgetCents = budget.trim() ? Math.round(parseFloat(budget) * 100) : null;
      if (budget.trim() && (!Number.isFinite(budgetCents) || (budgetCents as number) <= 0))
        throw new ApiError(0, "Budget must be a positive amount");
      const job = await api.createJob({
        trade,
        title: title.trim(),
        description: description.trim(),
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        address_text: address.trim(),
        budget_cents: budgetCents,
        customer_provides_supplies: supplies,
      });
      setTitle("");
      setDescription("");
      setAddress("");
      setBudget("");
      navigation.navigate("JobDetail", { jobId: job.id });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not post job");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Title>Post a job</Title>
      <Subtext>Trade</Subtext>
      <Row>
        {TRADES.map((t) => (
          <Button
            key={t}
            label={trade === t ? `✓ ${t}` : t}
            variant={trade === t ? "primary" : "secondary"}
            onPress={() => setTrade(t)}
          />
        ))}
      </Row>
      <Input label="Title" value={title} onChangeText={setTitle} autoCapitalize="sentences" />
      <Input
        label="Description"
        value={description}
        onChangeText={setDescription}
        multiline
        numberOfLines={3}
        autoCapitalize="sentences"
      />
      <Input label="Address" value={address} onChangeText={setAddress} autoCapitalize="words" />
      <Input
        label="Budget (optional, e.g. 120.00)"
        value={budget}
        onChangeText={setBudget}
        keyboardType="decimal-pad"
      />
      <Row>
        <Switch value={supplies} onValueChange={setSupplies} />
        <Subtext>I will provide supplies/materials</Subtext>
      </Row>
      <ErrorText message={error} />
      <Button
        label="Post job"
        onPress={submit}
        loading={busy}
        disabled={!title.trim() || !address.trim()}
      />
    </Screen>
  );
}
