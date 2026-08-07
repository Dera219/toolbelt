import * as Location from "expo-location";
import React, { useEffect, useState } from "react";
import { Switch } from "react-native";
import { ApiError, api } from "../../api/client";
import type { WorkerProfile } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { TRADES } from "../../config";
import { Badge, Button, Card, ErrorText, Input, Row, Screen, Subtext, Title } from "../../ui";

export default function WorkerProfileEditScreen() {
  const { user, refreshMe } = useAuth();
  const [profile, setProfile] = useState<WorkerProfile | null>(null);
  const [trade, setTrade] = useState("cleaning");
  const [bio, setBio] = useState("");
  const [rate, setRate] = useState("45.00");
  const [radius, setRadius] = useState("25");
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

  const run = async (fn: () => Promise<void>) => {
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

  const save = () =>
    run(async () => {
      const rateCents = Math.round(parseFloat(rate) * 100);
      const radiusKm = parseFloat(radius);
      if (!Number.isFinite(rateCents) || rateCents <= 0)
        throw new ApiError(0, "Hourly rate must be a positive amount");
      if (!Number.isFinite(radiusKm) || radiusKm <= 0 || radiusKm > 100)
        throw new ApiError(0, "Service radius must be 1–100 km");
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") throw new ApiError(0, "Location permission is required");
      const pos = await Location.getCurrentPositionAsync({});
      setProfile(
        await api.saveWorkerProfile({
          trade,
          bio: bio.trim(),
          hourly_rate_cents: rateCents,
          base_lat: pos.coords.latitude,
          base_lng: pos.coords.longitude,
          service_radius_km: radiusKm,
          is_available: available,
          has_own_tools: hasTools,
          has_vehicle: hasVehicle,
        })
      );
      setNotice("Profile saved");
    });

  const sendCode = () =>
    run(async () => {
      await api.requestPhoneCode(phone.trim());
      setCodeSent(true);
      setNotice("Code sent by SMS");
    });

  const confirmCode = () =>
    run(async () => {
      await api.verifyPhone(code.trim());
      await refreshMe();
      setNotice("Phone verified");
    });

  const submitVetting = () =>
    run(async () => {
      setProfile(await api.submitVetting());
      setNotice("Submitted for review");
    });

  return (
    <Screen>
      <Title>Worker profile</Title>
      {profile && (
        <Row>
          <Badge label={profile.vetting_status} />
          <Subtext>
            {profile.jobs_completed} jobs done
            {profile.rating_avg != null ? ` · ★ ${profile.rating_avg.toFixed(1)}` : ""}
          </Subtext>
        </Row>
      )}
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
      <Input label="Bio" value={bio} onChangeText={setBio} multiline autoCapitalize="sentences" />
      <Input label="Hourly rate" value={rate} onChangeText={setRate} keyboardType="decimal-pad" />
      <Input
        label="Service radius (km)"
        value={radius}
        onChangeText={setRadius}
        keyboardType="number-pad"
      />
      <Row>
        <Switch value={hasTools} onValueChange={setHasTools} />
        <Subtext>I have my own tools</Subtext>
      </Row>
      <Row>
        <Switch value={hasVehicle} onValueChange={setHasVehicle} />
        <Subtext>I have a vehicle</Subtext>
      </Row>
      <Row>
        <Switch value={available} onValueChange={setAvailable} />
        <Subtext>Available for work</Subtext>
      </Row>
      <Button label="Save profile (uses current location)" onPress={save} loading={busy} />

      {user && !user.phone_verified && (
        <Card>
          <Title>Verify your phone</Title>
          <Subtext>Required before vetting. Use E.164 format, e.g. +14155550123.</Subtext>
          <Input label="Phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" />
          <Button label="Send code" variant="secondary" onPress={sendCode} loading={busy} />
          {codeSent && (
            <>
              <Input
                label="6-digit code"
                value={code}
                onChangeText={setCode}
                keyboardType="number-pad"
                maxLength={6}
              />
              <Button label="Verify" onPress={confirmCode} loading={busy} />
            </>
          )}
        </Card>
      )}

      {profile &&
        user?.phone_verified &&
        (profile.vetting_status === "unverified" || profile.vetting_status === "rejected") && (
          <Button label="Submit for vetting" onPress={submitVetting} loading={busy} />
        )}
      <ErrorText message={error} />
      {notice && <Subtext>{notice}</Subtext>}
    </Screen>
  );
}
