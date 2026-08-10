import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import React, { useCallback, useState } from "react";
import { Image, RefreshControl, View } from "react-native";
import { api, money } from "../../api/client";
import type { Job } from "../../api/types";
import type { RootStackParamList } from "../../navigation";
import { API_URL } from "../../config";
import { space, tradeMeta } from "../../theme";
import {
  Badge,
  Button,
  Caption,
  Card,
  EmptyState,
  FadeIn,
  Price,
  Row,
  Screen,
  SectionHeader,
  Skeleton,
  StatusRail,
  Subtext,
  Title,
} from "../../ui";

const ACTIVE = new Set(["open", "assigned", "in_progress"]);

export default function MyJobsScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setJobs(await api.myJobs());
    } catch {
      setJobs((prev) => prev ?? []);
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
        <Skeleton height={120} />
        <Skeleton height={120} />
        <Skeleton height={120} />
      </Screen>
    );

  const active = jobs.filter((j) => ACTIVE.has(j.status));
  const past = jobs.filter((j) => !ACTIVE.has(j.status));

  return (
    <Screen refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}>
      {jobs.length === 0 && (
        <EmptyState
          icon="📋"
          title="No jobs yet"
          body="Post your first job and nearby pros will send you offers — usually within minutes."
          action={
            <Button
              label="Post a job"
              icon="➕"
              onPress={() => navigation.navigate("Main", { screen: "Post" } as never)}
            />
          }
        />
      )}

      {active.length > 0 && <SectionHeader title={`Active · ${active.length}`} />}
      {active.map((job, i) => (
        <JobCard key={job.id} job={job} delay={i * 60} showRail />
      ))}

      {past.length > 0 && <SectionHeader title="History" />}
      {past.map((job, i) => (
        <JobCard key={job.id} job={job} delay={i * 60} />
      ))}
    </Screen>
  );
}

export function JobCard({
  job,
  delay = 0,
  showRail = false,
  distanceKm,
}: {
  job: Job;
  delay?: number;
  showRail?: boolean;
  distanceKm?: number;
}) {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const trade = tradeMeta(job.trade);
  const cover = job.photos?.[0];

  return (
    <FadeIn delay={delay}>
      <Card onPress={() => navigation.navigate("JobDetail", { jobId: job.id })}>
        <Row between>
          <Row gap={space.sm}>
            <View
              style={{
                width: 40,
                height: 40,
                borderRadius: 12,
                backgroundColor: "#fff4dc",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Title>{trade.icon}</Title>
            </View>
            <View>
              <Title>{job.title}</Title>
              <Caption>
                {trade.label}
                {distanceKm != null ? ` · ${distanceKm.toFixed(1)} km away` : ""}
              </Caption>
            </View>
          </Row>
          {cover ? (
            <Image
              source={{ uri: `${API_URL}${cover.url}` }}
              style={{ width: 52, height: 52, borderRadius: 12 }}
            />
          ) : null}
        </Row>

        <Row between>
          <Badge label={job.status} />
          {job.budget_cents != null ? (
            <Price value={money(job.budget_cents, job.currency)} />
          ) : (
            <Subtext>Open to offers</Subtext>
          )}
        </Row>

        {showRail && <StatusRail status={job.status} />}
        <Caption>{job.address_text}</Caption>
      </Card>
    </FadeIn>
  );
}
