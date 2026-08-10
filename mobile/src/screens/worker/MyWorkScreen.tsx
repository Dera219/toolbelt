import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import React, { useCallback, useState } from "react";
import { RefreshControl, View } from "react-native";
import { api, money } from "../../api/client";
import type { Balance, Job, WorkerProfile } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import type { RootStackParamList } from "../../navigation";
import { space } from "../../theme";
import {
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
  Subtext,
  Title,
} from "../../ui";
import { JobCard } from "../customer/MyJobsScreen";

const ACTIVE = new Set(["assigned", "in_progress"]);

export default function MyWorkScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { user } = useAuth();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [profile, setProfile] = useState<WorkerProfile | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const [mine, bal, prof] = await Promise.allSettled([
      api.myJobs(),
      api.balance(),
      api.getWorkerProfile(),
    ]);
    setJobs(
      mine.status === "fulfilled"
        ? mine.value.filter((j) => j.assigned_worker_id === user?.id)
        : []
    );
    if (bal.status === "fulfilled") setBalance(bal.value);
    setProfile(prof.status === "fulfilled" ? prof.value : null);
  }, [user?.id]);

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
        <Skeleton height={120} />
      </Screen>
    );

  const active = jobs.filter((j) => ACTIVE.has(j.status));
  const done = jobs.filter((j) => !ACTIVE.has(j.status));

  return (
    <Screen refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}>
      <FadeIn>
        <Card raised>
          <Row between>
            <View>
              <Caption>PENDING BALANCE</Caption>
              <Price
                value={balance ? money(balance.balance_cents, balance.currency) : "—"}
                size="lg"
              />
            </View>
            {profile != null && (
              <View style={{ alignItems: "flex-end" }}>
                <Caption>RATING</Caption>
                <Title>
                  {profile.rating_avg != null ? `★ ${profile.rating_avg.toFixed(1)}` : "—"}
                </Title>
                <Caption>{profile.jobs_completed} jobs done</Caption>
              </View>
            )}
          </Row>
          <Subtext>Earnings transfer automatically once your payout account is active.</Subtext>
          <Button
            label="Payout settings"
            variant="secondary"
            size="sm"
            onPress={() => navigation.navigate("Main", { screen: "Account" } as never)}
          />
        </Card>
      </FadeIn>

      {jobs.length === 0 && (
        <EmptyState
          icon="🔧"
          title="No booked work yet"
          body="Send offers on nearby jobs — customers usually book within a few hours."
          action={
            <Button
              label="Find jobs near me"
              onPress={() => navigation.navigate("Main", { screen: "Nearby" } as never)}
            />
          }
        />
      )}

      {active.length > 0 && <SectionHeader title={`Active · ${active.length}`} />}
      {active.map((job, i) => (
        <JobCard key={job.id} job={job} delay={i * 60} showRail />
      ))}

      {done.length > 0 && <SectionHeader title="Completed" />}
      {done.map((job, i) => (
        <JobCard key={job.id} job={job} delay={i * 60} />
      ))}
    </Screen>
  );
}
