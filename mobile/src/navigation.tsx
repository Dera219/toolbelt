import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import React from "react";
import { Text } from "react-native";
import { useAuth } from "./auth/AuthContext";
import AccountScreen from "./screens/AccountScreen";
import ChatScreen from "./screens/ChatScreen";
import JobDetailScreen from "./screens/JobDetailScreen";
import LoginScreen from "./screens/LoginScreen";
import RegisterScreen from "./screens/RegisterScreen";
import MyJobsScreen from "./screens/customer/MyJobsScreen";
import PostJobScreen from "./screens/customer/PostJobScreen";
import MyWorkScreen from "./screens/worker/MyWorkScreen";
import NearbyJobsScreen from "./screens/worker/NearbyJobsScreen";
import WorkerProfileEditScreen from "./screens/worker/WorkerProfileEditScreen";
import { Loading, colors } from "./ui";

export type RootStackParamList = {
  Login: undefined;
  Register: undefined;
  Main: undefined;
  JobDetail: { jobId: number };
  Chat: { jobId: number; workerId: number };
  WorkerProfileEdit: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tabs = createBottomTabNavigator();

const icon =
  (glyph: string) =>
  ({ color }: { color: string }) => <Text style={{ fontSize: 20, color }}>{glyph}</Text>;

function MainTabs() {
  const { mode } = useAuth();
  return (
    <Tabs.Navigator
      key={mode} // reset tabs when switching between hiring and working
      screenOptions={{
        tabBarActiveTintColor: colors.text,
        tabBarInactiveTintColor: colors.subtext,
        tabBarLabelStyle: { fontSize: 11, fontWeight: "700" },
        tabBarStyle: {
          backgroundColor: colors.card,
          borderTopColor: colors.border,
          height: 62,
          paddingTop: 6,
        },
        headerStyle: { backgroundColor: colors.bg },
        headerTitleStyle: { fontWeight: "800" as const, fontSize: 19 },
        headerShadowVisible: false,
      }}
    >
      {mode === "customer" ? (
        <>
          <Tabs.Screen
            name="MyJobs"
            component={MyJobsScreen}
            options={{ title: "My jobs", tabBarIcon: icon("🧾") }}
          />
          <Tabs.Screen
            name="Post"
            component={PostJobScreen}
            options={{ title: "Post a job", tabBarIcon: icon("➕") }}
          />
        </>
      ) : (
        <>
          <Tabs.Screen
            name="Nearby"
            component={NearbyJobsScreen}
            options={{ title: "Nearby jobs", tabBarIcon: icon("📍") }}
          />
          <Tabs.Screen
            name="MyWork"
            component={MyWorkScreen}
            options={{ title: "My work", tabBarIcon: icon("🔧") }}
          />
        </>
      )}
      <Tabs.Screen
        name="Account"
        component={AccountScreen}
        options={{ title: "Account", tabBarIcon: icon("👤") }}
      />
    </Tabs.Navigator>
  );
}

export function RootNavigator() {
  const { booting, user } = useAuth();
  if (booting) return <Loading />;
  return (
    <Stack.Navigator
      screenOptions={{
        headerShadowVisible: false,
        headerStyle: { backgroundColor: colors.bg },
        headerTitleStyle: { fontWeight: "800" as const, fontSize: 19 },
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      {user == null ? (
        <>
          <Stack.Screen name="Login" component={LoginScreen} options={{ headerShown: false }} />
          <Stack.Screen
            name="Register"
            component={RegisterScreen}
            options={{ title: "Sign up" }}
          />
        </>
      ) : (
        <>
          <Stack.Screen name="Main" component={MainTabs} options={{ headerShown: false }} />
          <Stack.Screen
            name="JobDetail"
            component={JobDetailScreen}
            options={{ title: "Job" }}
          />
          <Stack.Screen name="Chat" component={ChatScreen} options={{ title: "Chat" }} />
          <Stack.Screen
            name="WorkerProfileEdit"
            component={WorkerProfileEditScreen}
            options={{ title: "Worker profile" }}
          />
        </>
      )}
    </Stack.Navigator>
  );
}
