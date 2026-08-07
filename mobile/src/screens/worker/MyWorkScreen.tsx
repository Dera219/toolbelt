import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import React, { useCallback, useState } from "react";
import { RefreshControl, ScrollView } from "react-native";
import { api, money } from "../../api/client";
import type { Balance, Job } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import type { RootStackParamList } from "../../navigation";
import { Badge, Card, Row, Subtext, Title, colors } from "../../ui";

export default function MyWorkScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { user } = useAuth();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [mine, bal] = await Promise.all([api.myJobs(), api.balance()]);
      setJobs(mine.filter((j) => j.assigned_worker_id === user?.id));
      setBalance(bal);
    } catch {
      // keep last good state
    }
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

  return (
    <ScrollView
      style={{ backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 16, gap: 12 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}
    >
      {balance !== null && (
        <Card>
          <Subtext>Pending balance</Subtext>
          <Title>{money(balance.balance_cents, balance.currency)}</Title>
          <Subtext>Captured earnings waiting for payout. Payouts land automatically.</Subtext>
        </Card>
      )}
      {jobs.length === 0 && (
        <Card>
          <Title>No booked work yet</Title>
          <Subtext>Make offers on nearby jobs to get booked.</Subtext>
        </Card>
      )}
      {jobs.map((job) => (
        <Card key={job.id} onPress={() => navigation.navigate("JobDetail", { jobId: job.id })}>
          <Title>{job.title}</Title>
          <Row>
            <Badge label={job.status} />
            <Badge label={job.trade} />
          </Row>
          <Subtext>{job.address_text}</Subtext>
        </Card>
      ))}
    </ScrollView>
  );
}
