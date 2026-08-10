import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import * as Location from "expo-location";
import React, { useCallback, useState } from "react";
import { RefreshControl } from "react-native";
import { ApiError, api } from "../../api/client";
import type { NearbyJob, WorkerProfile } from "../../api/types";
import type { RootStackParamList } from "../../navigation";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  Row,
  Screen,
  SectionHeader,
  Skeleton,
  Subtext,
  Title,
} from "../../ui";
import { JobCard } from "../customer/MyJobsScreen";

export default function NearbyJobsScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [jobs, setJobs] = useState<NearbyJob[] | null>(null);
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
        setJobs([]);
      } else {
        setError(e instanceof ApiError ? e.message : "Could not load jobs");
        setJobs((prev) => prev ?? []);
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

  if (jobs === null)
    return (
      <Screen>
        <Skeleton height={110} />
        <Skeleton height={110} />
        <Skeleton height={110} />
      </Screen>
    );

  return (
    <Screen refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}>
      {profile === null && (
        <EmptyState
          icon="🧰"
          title="Set up your worker profile"
          body="Tell us your trade, rate, and how far you'll travel — then jobs near you show up here."
          action={
            <Button label="Set up profile" onPress={() => navigation.navigate("WorkerProfileEdit")} />
          }
        />
      )}

      {profile !== null && profile.vetting_status !== "verified" && (
        <Card raised>
          <Row between>
            <Title>Vetting</Title>
            <Badge label={profile.vetting_status} />
          </Row>
          <Subtext>
            Browse freely — but you'll need to be verified before you can send offers.
          </Subtext>
          <Button
            label="Continue verification"
            variant="accent"
            onPress={() => navigation.navigate("WorkerProfileEdit")}
          />
        </Card>
      )}

      <ErrorText message={error} />

      {profile !== null && jobs.length === 0 && !error && (
        <EmptyState
          icon="📍"
          title="No open jobs nearby"
          body="Nothing in your area right now. Pull down to refresh, or widen your service radius."
          action={
            <Button
              label="Widen my radius"
              variant="secondary"
              onPress={() => navigation.navigate("WorkerProfileEdit")}
            />
          }
        />
      )}

      {jobs.length > 0 && <SectionHeader title={`${jobs.length} job${jobs.length > 1 ? "s" : ""} near you`} />}
      {jobs.map((job, i) => (
        <JobCard key={job.id} job={job} delay={i * 60} distanceKm={job.distance_km} />
      ))}
    </Screen>
  );
}
