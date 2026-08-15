import React, { useEffect, useState } from "react";
import { Switch, View } from "react-native";
import { ApiError, api } from "../../api/client";
import type { WorkerProfile } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { TRADES } from "../../config";
import { resolveCoords } from "../../location";
import { colors, palette, space, tradeMeta } from "../../theme";
import {
  Badge,
  Body,
  Button,
  Caption,
  Card,
  ErrorText,
  FadeIn,
  Input,
  NoticeText,
  Row,
  Screen,
  SectionHeader,
  Subtext,
  Title,
  successFeedback,
} from "../../ui";

const VETTING_COPY: Record<string, string> = {
  unverified: "Verify your phone, then submit for review. Most checks clear within a day.",
  pending: "We're reviewing your details. You'll be able to send offers once approved.",
  verified: "You're verified — you can send offers on any job in your trade.",
  rejected: "Your submission wasn't approved. Update your details and try again.",
};

export default function WorkerProfileEditScreen() {
  const { user, refreshMe } = useAuth();
  const [profile, setProfile] = useState<WorkerProfile | null>(null);
  const [trade, setTrade] = useState("cleaning");
  const [bio, setBio] = useState("");
  const [rate, setRate] = useState("45.00");
  const [radius, setRadius] = useState("25");
  const [baseAddress, setBaseAddress] = useState("");
  const [hasTools, setHasTools] = useState(true);
  const [hasVehicle, setHasVehicle] = useState(false);
  const [available, setAvailable] = useState(true);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .getWorkerProfile()
      .then((p) => {
        setProfile(p);
        setTrade(p.trade);
        setBio(p.bio);
        setRate((p.hourly_rate_cents / 100).toFixed(2));
        setRadius(String(p.service_radius_km));
        setHasTools(p.has_own_tools);
        setHasVehicle(p.has_vehicle);
        setAvailable(p.is_available);
      })
      .catch(() => setProfile(null));
  }, []);

  const run = (fn: () => Promise<void>) => async () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await fn();
      successFeedback();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const save = run(async () => {
    const rateCents = Math.round(parseFloat(rate) * 100);
    const radiusKm = parseFloat(radius);
    if (!Number.isFinite(rateCents) || rateCents <= 0)
      throw new ApiError(0, "Hourly rate must be a positive amount");
    if (!Number.isFinite(radiusKm) || radiusKm <= 0 || radiusKm > 100)
      throw new ApiError(0, "Service radius must be 1–100 km");
    // Keeping the existing base is the last resort: without it, a worker who
    // only wanted to edit their rate would be blocked by a location prompt.
    const { lat, lng, source } = await resolveCoords({
      address: baseAddress,
      saved: profile ? { lat: profile.base_lat, lng: profile.base_lng } : null,
      unavailableMessage:
        "Allow location access, or type the town or address you work out of so we know where to send jobs.",
    });
    setProfile(
      await api.saveWorkerProfile({
        trade,
        bio: bio.trim(),
        hourly_rate_cents: rateCents,
        base_lat: lat,
        base_lng: lng,
        service_radius_km: radiusKm,
        is_available: available,
        has_own_tools: hasTools,
        has_vehicle: hasVehicle,
      })
    );
    setNotice(
      source === "gps"
        ? "Profile saved"
        : source === "address"
          ? "Profile saved — base location set from the address you entered"
          : "Profile saved — your existing base location was kept"
    );
  });

  const sendCode = run(async () => {
    await api.requestPhoneCode(phone.trim());
    setCodeSent(true);
    setNotice("Code sent by SMS");
  });

  const confirmCode = run(async () => {
    await api.verifyPhone(code.trim());
    await refreshMe();
    setNotice("Phone verified");
  });

  const submitVetting = run(async () => {
    setProfile(await api.submitVetting());
    setNotice("Submitted for review");
  });

  const status = profile?.vetting_status ?? "unverified";

  return (
    <Screen>
      <FadeIn>
        <Card raised>
          <Row between>
            <Title>Verification</Title>
            <Badge label={status} />
          </Row>
          <Body>{VETTING_COPY[status]}</Body>
          {profile != null && (
            <Row gap={space.lg}>
              <View>
                <Caption>JOBS DONE</Caption>
                <Title>{profile.jobs_completed}</Title>
              </View>
              <View>
                <Caption>RATING</Caption>
                <Title>
                  {profile.rating_avg != null ? `★ ${profile.rating_avg.toFixed(1)}` : "—"}
                </Title>
              </View>
            </Row>
          )}
        </Card>
      </FadeIn>

      {user && !user.phone_verified && (
        <FadeIn delay={60}>
          <Card>
            <Title>Step 1 · Verify your phone</Title>
            <Input
              label="Phone number"
              hint="International format, e.g. +14155550123"
              value={phone}
              onChangeText={setPhone}
              placeholder="+14155550123"
              keyboardType="phone-pad"
            />
            <Button label="Send code" variant="secondary" onPress={sendCode} loading={busy} />
            {codeSent && (
              <>
                <Input
                  label="6-digit code"
                  value={code}
                  onChangeText={setCode}
                  keyboardType="number-pad"
                  maxLength={6}
                  placeholder="123456"
                />
                <Button label="Verify phone" variant="accent" onPress={confirmCode} loading={busy} />
              </>
            )}
          </Card>
        </FadeIn>
      )}

      <SectionHeader title="Your trade" />
      <FadeIn delay={90}>
        <Card>
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

      <SectionHeader title="Rate & area" />
      <FadeIn delay={120}>
        <Card>
          <Input
            label="Hourly rate"
            prefix="$"
            value={rate}
            onChangeText={setRate}
            keyboardType="decimal-pad"
          />
          <Input
            label="Service radius (km)"
            hint="How far you'll travel from your base location."
            value={radius}
            onChangeText={setRadius}
            keyboardType="number-pad"
          />
          <Input
            label="Base address"
            hint="Only used if location access is off. Town and state is enough."
            value={baseAddress}
            onChangeText={setBaseAddress}
            placeholder="College Park, MD"
            autoCapitalize="words"
          />
          <Input
            label="Bio"
            hint="Customers read this before booking."
            value={bio}
            onChangeText={setBio}
            placeholder="8 years of residential cleaning. Own supplies."
            multiline
            autoCapitalize="sentences"
          />
        </Card>
      </FadeIn>

      <SectionHeader title="What you bring" />
      <FadeIn delay={150}>
        <Card>
          <Toggle
            label="I have my own tools"
            hint="Shown on your offers"
            value={hasTools}
            onChange={setHasTools}
          />
          <Toggle
            label="I have a vehicle"
            hint="Needed for moving and hauling jobs"
            value={hasVehicle}
            onChange={setHasVehicle}
          />
          <Toggle
            label="Available for work"
            hint="Turn off to pause new offers"
            value={available}
            onChange={setAvailable}
          />
        </Card>
      </FadeIn>

      <ErrorText message={error} />
      <NoticeText message={notice} />
      <Button label="Save profile" icon="📍" onPress={save} loading={busy} />
      <Caption>
        Saving sets your base location to where you are now — or to the base address above if
        location access is off.
      </Caption>

      {profile && user?.phone_verified && (status === "unverified" || status === "rejected") && (
        <Button label="Submit for vetting" variant="accent" onPress={submitVetting} loading={busy} />
      )}
    </Screen>
  );
}

function Toggle({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <Row between>
      <View style={{ flex: 1, paddingRight: space.md }}>
        <Body>{label}</Body>
        <Caption>{hint}</Caption>
      </View>
      <Switch
        value={value}
        onValueChange={onChange}
        trackColor={{ true: palette.amber, false: palette.line }}
      />
    </Row>
  );
}
