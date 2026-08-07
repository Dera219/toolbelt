import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ApiError, api } from "../api/client";
import type { Message } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import type { RootStackParamList } from "../navigation";
import { Button, ErrorText, colors } from "../ui";

type Props = NativeStackScreenProps<RootStackParamList, "Chat">;
const POLL_MS = 4000;

export default function ChatScreen({ route }: Props) {
  const { jobId, workerId } = route.params;
  const { user } = useAuth();
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const listRef = useRef<FlatList<Message>>(null);

  const load = useCallback(async () => {
    try {
      setMessages(await api.messages(jobId, workerId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load messages");
    }
  }, [jobId, workerId]);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const send = async () => {
    const body = draft.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      await api.sendMessage(jobId, workerId, body);
      setDraft("");
      await load();
      listRef.current?.scrollToEnd({ animated: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not send");
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: colors.bg }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={90}
    >
      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={(m) => String(m.id)}
        contentContainerStyle={{ padding: 16, gap: 8 }}
        onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
        renderItem={({ item }) => {
          const mine = item.sender_id === user?.id;
          return (
            <View style={[styles.bubble, mine ? styles.mine : styles.theirs]}>
              <Text style={{ color: mine ? "#fff" : colors.text }}>{item.body}</Text>
              <Text style={[styles.time, { color: mine ? "#dbe6ff" : colors.subtext }]}>
                {new Date(item.created_at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </Text>
            </View>
          );
        }}
      />
      <ErrorText message={error} />
      <View style={[styles.composer, { paddingBottom: insets.bottom + 8 }]}>
        <TextInput
          style={styles.input}
          value={draft}
          onChangeText={setDraft}
          placeholder="Message…"
          placeholderTextColor={colors.subtext}
          multiline
        />
        <Button label="Send" onPress={send} loading={busy} disabled={!draft.trim()} />
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  bubble: { maxWidth: "80%", borderRadius: 14, padding: 10, gap: 2 },
  mine: { alignSelf: "flex-end", backgroundColor: colors.primary },
  theirs: {
    alignSelf: "flex-start",
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
  },
  time: { fontSize: 10, alignSelf: "flex-end" },
  composer: {
    flexDirection: "row",
    gap: 8,
    padding: 12,
    backgroundColor: colors.card,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    alignItems: "flex-end",
  },
  input: {
    flex: 1,
    backgroundColor: colors.bg,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 10,
    maxHeight: 120,
    fontSize: 16,
    color: colors.text,
  },
});
