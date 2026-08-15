import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import React, { useCallback, useState } from "react";
import { RefreshControl } from "react-native";
import { ApiError, api } from "../../api/client";
import type { NearbyJob, WorkerProfile } from "../../api/types";
import { resolveCoords } from "../../location";
import type { RootStackParamList } from "../../navigation";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  NoticeText,
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
  const [usingBaseLocation, setUsingBaseLocation] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setUsingBaseLocation(false);
    try {
      const me = await api.getWorkerProfile();
      setProfile(me);
      // This screen collects no address, but the worker already told us where
      // they are based when they set up their profile — that is the right
      // centre for "jobs near me" when live location is unavailable.
      const { lat, lng, source } = await resolveCoords({
        saved: { lat: me.base_lat, lng: me.base_lng },
        unavailableMessage:
          "Allow location access to see jobs around you, or set your base location in your worker profile.",
      });
      setUsingBaseLocation(source === "saved");
      setJobs(await api.nearbyJobs(lat, lng, me.trade));
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
      <NoticeText
        message={
          usingBaseLocation ? "Showing jobs around your saved base location." : null
        }
      />

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
