import { useFocusEffect, useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import React, { useCallback, useState } from "react";
import { RefreshControl, ScrollView, Text } from "react-native";
import { api, money } from "../../api/client";
import type { Job } from "../../api/types";
import type { RootStackParamList } from "../../navigation";
import { Badge, Card, Row, Subtext, Title, colors } from "../../ui";

export default function MyJobsScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setJobs(await api.myJobs());
    } catch {
      // pull-to-refresh will surface issues; keep the last good list
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
      {jobs.length === 0 && (
        <Card>
          <Title>No jobs yet</Title>
          <Subtext>Post your first job from the Post tab.</Subtext>
        </Card>
      )}
      {jobs.map((job) => (
        <Card key={job.id} onPress={() => navigation.navigate("JobDetail", { jobId: job.id })}>
          <Row>
            <Title>{job.title}</Title>
          </Row>
          <Row>
            <Badge label={job.status} />
            <Badge label={job.trade} />
          </Row>
          <Subtext>
            {job.address_text}
            {job.budget_cents != null ? ` · budget ${money(job.budget_cents, job.currency)}` : ""}
          </Subtext>
          <Text style={{ color: colors.subtext, fontSize: 12 }}>
            {new Date(job.created_at).toLocaleDateString()}
          </Text>
        </Card>
      ))}
    </ScrollView>
  );
}
