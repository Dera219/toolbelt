import * as SecureStore from "expo-secure-store";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, setAuthToken } from "../api/client";
import type { Role, User } from "../api/types";

export type Mode = "customer" | "worker";
const TOKEN_KEY = "toolbelt_token";
const MODE_KEY = "toolbelt_mode";

interface AuthState {
  booting: boolean;
  user: User | null;
  mode: Mode;
  canWork: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string, role: Role) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
  setMode: (mode: Mode) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [booting, setBooting] = useState(true);
  const [user, setUser] = useState<User | null>(null);
  const [mode, setModeState] = useState<Mode>("customer");

  useEffect(() => {
    (async () => {
      try {
        const token = await SecureStore.getItemAsync(TOKEN_KEY);
        if (token) {
          setAuthToken(token);
          setUser(await api.me());
          const savedMode = await SecureStore.getItemAsync(MODE_KEY);
          if (savedMode === "worker" || savedMode === "customer") setModeState(savedMode);
        }
      } catch {
        setAuthToken(null);
        await SecureStore.deleteItemAsync(TOKEN_KEY);
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await api.login(email, password);
    setAuthToken(access_token);
    await SecureStore.setItemAsync(TOKEN_KEY, access_token);
    const me = await api.me();
    setUser(me);
    if (me.role === "worker") setModeState("worker");
  }, []);

  const register = useCallback(
    async (email: string, password: string, fullName: string, role: Role) => {
      await api.register({ email, password, full_name: fullName, role });
      await login(email, password);
    },
    [login]
  );

  const logout = useCallback(async () => {
    setAuthToken(null);
    setUser(null);
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  }, []);

  const refreshMe = useCallback(async () => {
    setUser(await api.me());
  }, []);

  const setMode = useCallback((next: Mode) => {
    setModeState(next);
    SecureStore.setItemAsync(MODE_KEY, next).catch(() => {});
  }, []);

  const canWork = user?.role === "worker" || user?.role === "both";
  const value = useMemo(
    () => ({
      booting,
      user,
      mode: canWork ? mode : "customer",
      canWork,
      login,
      register,
      logout,
      refreshMe,
      setMode,
    }),
    [booting, user, mode, canWork, login, register, logout, refreshMe, setMode]
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
