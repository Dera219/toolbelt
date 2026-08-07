import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import * as Location from "expo-location";
import React, { useCallback, useState } from "react";
import { RefreshControl, ScrollView } from "react-native";
import { ApiError, api, money } from "../../api/client";
import type { NearbyJob, WorkerProfile } from "../../api/types";
import type { RootStackParamList } from "../../navigation";
import { Badge, Button, Card, ErrorText, Row, Subtext, Title, colors } from "../../ui";

export default function NearbyJobsScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [jobs, setJobs] = useState<NearbyJob[]>([]);
  const [profile, setProfile] = useState<WorkerProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const me = await api.getWorkerProfile();
      setProfile(me);
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") throw new ApiError(0, "Location permission is required");
      const pos = await Location.getCurrentPositionAsync({});
      setJobs(await api.nearbyJobs(pos.coords.latitude, pos.coords.longitude, me.trade));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setProfile(null);
      } else {
        setError(e instanceof ApiError ? e.message : "Could not load jobs");
      }
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  return (
    <ScrollView
      style={{ backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 16, gap: 12 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}
    >
      {profile === null && (
        <Card>
          <Title>Set up your worker profile</Title>
          <Subtext>Pick your trade, rate, and service area to see jobs near you.</Subtext>
          <Button
            label="Set up profile"
            onPress={() => navigation.navigate("WorkerProfileEdit")}
          />
        </Card>
      )}
      {profile !== null && profile.vetting_status !== "verified" && (
        <Card>
          <Title>Vetting: {profile.vetting_status}</Title>
          <Subtext>
            You can browse jobs, but you need to be verified before you can make offers.
          </Subtext>
          <Button label="Go to profile" onPress={() => navigation.navigate("WorkerProfileEdit")} />
        </Card>
      )}
      <ErrorText message={error} />
      {profile !== null && jobs.length === 0 && !error && (
        <Card>
          <Title>No open jobs nearby</Title>
          <Subtext>Pull to refresh, or widen your service radius in your profile.</Subtext>
        </Card>
      )}
      {jobs.map((job) => (
        <Card key={job.id} onPress={() => navigation.navigate("JobDetail", { jobId: job.id })}>
          <Title>{job.title}</Title>
          <Row>
            <Badge label={job.trade} />
            <Subtext>{job.distance_km.toFixed(1)} km away</Subtext>
          </Row>
          <Subtext>
            {job.budget_cents != null
              ? `Budget ${money(job.budget_cents, job.currency)}`
              : "Open to offers"}
            {job.customer_provides_supplies ? " · supplies provided" : ""}
          </Subtext>
        </Card>
      ))}
    </ScrollView>
  );
}
