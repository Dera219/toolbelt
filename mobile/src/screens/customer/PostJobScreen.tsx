import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import * as ImagePicker from "expo-image-picker";
import * as Location from "expo-location";
import React, { useState } from "react";
import { Image, Switch, View } from "react-native";
import { ApiError, api } from "../../api/client";
import { TRADES } from "../../config";
import type { RootStackParamList } from "../../navigation";
import { colors, palette, radius, space, tradeMeta } from "../../theme";
import {
  Body,
  Button,
  Caption,
  Card,
  Choice,
  ErrorText,
  FadeIn,
  Input,
  Row,
  Screen,
  SectionHeader,
  Subtext,
  Title,
  successFeedback,
} from "../../ui";

const MAX_PHOTOS = 8;

export default function PostJobScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [trade, setTrade] = useState<string>("cleaning");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [address, setAddress] = useState("");
  const [budget, setBudget] = useState("");
  const [supplies, setSupplies] = useState(false);
  const [photoUris, setPhotoUris] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pickPhotos = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      allowsMultipleSelection: true,
      selectionLimit: MAX_PHOTOS - photoUris.length,
      quality: 0.7,
    });
    if (!result.canceled)
      setPhotoUris((prev) => [...prev, ...result.assets.map((a) => a.uri)].slice(0, MAX_PHOTOS));
  };

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
      for (const uri of photoUris) {
        await api.uploadJobPhoto(job.id, uri); // photos ride along after the job exists
      }
      successFeedback();
      setTitle("");
      setDescription("");
      setAddress("");
      setBudget("");
      setPhotoUris([]);
      navigation.navigate("JobDetail", { jobId: job.id });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not post job");
    } finally {
      setBusy(false);
    }
  };

  const ready = title.trim().length > 0 && address.trim().length > 0;

  return (
    <Screen>
      <FadeIn>
        <Card raised>
          <Title>What do you need done?</Title>
          <Subtext>Pick a trade — pros in that trade get notified.</Subtext>
          <Row gap={space.sm}>
            {TRADES.map((t) => {
              const meta = tradeMeta(t);
              const active = trade === t;
              return (
                <View key={t} style={{ width: "31%" }}>
                  <Card
                    onPress={() => setTrade(t)}
                    padded={false}
                    style={{
                      alignItems: "center",
                      paddingVertical: space.md,
                      gap: 4,
                      borderColor: active ? palette.amber : colors.border,
                      borderWidth: active ? 2 : 1,
                      backgroundColor: active ? palette.amberSoft : colors.card,
                    }}
                  >
                    <Title>{meta.icon}</Title>
                    <Caption>{meta.label}</Caption>
                  </Card>
                </View>
              );
            })}
          </Row>
        </Card>
      </FadeIn>

      <SectionHeader title="Details" />
      <FadeIn delay={60}>
        <Card>
          <Input
            label="Title"
            value={title}
            onChangeText={setTitle}
            placeholder="Deep clean 2BR apartment"
            autoCapitalize="sentences"
          />
          <Input
            label="Description"
            hint="The more detail, the more accurate the offers."
            value={description}
            onChangeText={setDescription}
            placeholder="Kitchen and both bathrooms. Oven needs attention."
            multiline
            numberOfLines={3}
            autoCapitalize="sentences"
          />
          <Input
            label="Address"
            value={address}
            onChangeText={setAddress}
            placeholder="College Park, MD"
            autoCapitalize="words"
          />
          <Input
            label="Budget (optional)"
            prefix="$"
            hint="Leave blank to let pros name their price."
            value={budget}
            onChangeText={setBudget}
            placeholder="120.00"
            keyboardType="decimal-pad"
          />
        </Card>
      </FadeIn>

      <SectionHeader title="Photos" />
      <FadeIn delay={90}>
        <Card>
          <Body>Photos help pros quote accurately — and cut surprise costs on the day.</Body>
          {photoUris.length > 0 && (
            <Row gap={space.sm}>
              {photoUris.map((uri) => (
                <View key={uri}>
                  <Image source={{ uri }} style={{ width: 68, height: 68, borderRadius: radius.md }} />
                </View>
              ))}
            </Row>
          )}
          <Row between>
            {photoUris.length < MAX_PHOTOS && (
              <View style={{ flex: 1 }}>
                <Button
                  label={photoUris.length ? "Add more" : "Add photos"}
                  icon="📷"
                  variant="secondary"
                  onPress={pickPhotos}
                />
              </View>
            )}
            {photoUris.length > 0 && (
              <Button
                label="Clear"
                variant="ghost"
                size="sm"
                full={false}
                onPress={() => setPhotoUris([])}
              />
            )}
          </Row>
        </Card>
      </FadeIn>

      <FadeIn delay={120}>
        <Card>
          <Row between>
            <View style={{ flex: 1, paddingRight: space.md }}>
              <Title>I'll provide supplies</Title>
              <Subtext>Cleaning products, materials, parts</Subtext>
            </View>
            <Switch
              value={supplies}
              onValueChange={setSupplies}
              trackColor={{ true: palette.amber, false: palette.line }}
            />
          </Row>
        </Card>
      </FadeIn>

      <ErrorText message={error} />
      <Button
        label="Post job"
        icon="📣"
        variant="accent"
        onPress={submit}
        loading={busy}
        disabled={!ready}
      />
      {!ready && <Caption>Add a title and address to post.</Caption>}
    </Screen>
  );
}
