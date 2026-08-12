import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  api,
  setAuthToken,
  setRefreshToken,
  setSessionLostHandler,
  setTokenRotationHandler,
} from "../api/client";
import type { Role, User } from "../api/types";
import { registerForPush, unregisterPush } from "../push";
import { tokenStore } from "./storage";
import { SocialCancelled, signInWithProvider, type SocialProvider } from "./social";

export type Mode = "customer" | "worker";
const TOKEN_KEY = "toolbelt_token";
const REFRESH_KEY = "toolbelt_refresh";
const MODE_KEY = "toolbelt_mode";

interface AuthState {
  booting: boolean;
  user: User | null;
  mode: Mode;
  canWork: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string, role: Role) => Promise<void>;
  signInWithSocial: (provider: SocialProvider, role?: Role) => Promise<void>;
  socialProviders: SocialProvider[];
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
  setMode: (mode: Mode) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [booting, setBooting] = useState(true);
  const [user, setUser] = useState<User | null>(null);
  const [mode, setModeState] = useState<Mode>("customer");
  const [socialProviders, setSocialProviders] = useState<SocialProvider[]>([]);

  useEffect(() => {
    (async () => {
      try {
        const token = await tokenStore.get(TOKEN_KEY);
        const refresh = await tokenStore.get(REFRESH_KEY);
        if (refresh) setRefreshToken(refresh);
        if (token) {
          setAuthToken(token);
          setUser(await api.me());
          void registerForPush(); // fire-and-forget: never delays app start
          const savedMode = await tokenStore.get(MODE_KEY);
          if (savedMode === "worker" || savedMode === "customer") setModeState(savedMode);
        }
      } catch {
        setAuthToken(null);
        setRefreshToken(null);
        await tokenStore.remove(TOKEN_KEY);
        await tokenStore.remove(REFRESH_KEY);
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  useEffect(() => {
    // The server is the source of truth for which buttons to show; a provider
    // without credentials configured would fail only after the user taps it.
    api
      .authProviders()
      .then(({ providers }) => setSocialProviders(providers as SocialProvider[]))
      .catch(() => setSocialProviders([]));
  }, []);

  const applyTokens = useCallback(
    async (access: string, refresh: string) => {
      setAuthToken(access);
      setRefreshToken(refresh);
      await tokenStore.set(TOKEN_KEY, access);
      await tokenStore.set(REFRESH_KEY, refresh);
      const me = await api.me();
      setUser(me);
      if (me.role === "worker") setModeState("worker");
    },
    []
  );

  const signInWithSocial = useCallback(
    async (provider: SocialProvider, role?: Role) => {
      let idToken: string;
      try {
        idToken = await signInWithProvider(provider);
      } catch (e) {
        // A cancelled sign-in is a normal outcome, not an error to shout about.
        if (e instanceof SocialCancelled) return;
        throw e;
      }
      const tokens = await api.socialSignIn(provider, idToken, role);
      await applyTokens(tokens.access_token, tokens.refresh_token);
    },
    [applyTokens]
  );

  const login = useCallback(async (email: string, password: string) => {
    const { access_token, refresh_token } = await api.login(email, password);
    setAuthToken(access_token);
    setRefreshToken(refresh_token);
    await tokenStore.set(TOKEN_KEY, access_token);
    await tokenStore.set(REFRESH_KEY, refresh_token);
    const me = await api.me();
    setUser(me);
    if (me.role === "worker") setModeState("worker");
    void registerForPush();
  }, []);

  const register = useCallback(
    async (email: string, password: string, fullName: string, role: Role) => {
      await api.register({ email, password, full_name: fullName, role });
      await login(email, password);
    },
    [login]
  );

  const logout = useCallback(async () => {
    // Release the device before clearing the auth token — the unregister call
    // needs to be authenticated, and the next person to log in on this phone
    // must not inherit these notifications.
    await unregisterPush();
    // Revoke the session server-side too; dropping the token locally would
    // leave a valid refresh token alive for its full 30 days.
    const refresh = await tokenStore.get(REFRESH_KEY);
    if (refresh) await api.logout(refresh).catch(() => {});
    setAuthToken(null);
    setRefreshToken(null);
    setUser(null);
    await tokenStore.remove(TOKEN_KEY);
    await tokenStore.remove(REFRESH_KEY);
  }, []);

  const refreshMe = useCallback(async () => {
    setUser(await api.me());
  }, []);

  const setMode = useCallback((next: Mode) => {
    setModeState(next);
    tokenStore.set(MODE_KEY, next).catch(() => {});
  }, []);

  useEffect(() => {
    setTokenRotationHandler((access, refresh) => {
      tokenStore.set(TOKEN_KEY, access).catch(() => {});
      tokenStore.set(REFRESH_KEY, refresh).catch(() => {});
    });
    setSessionLostHandler(() => {
      // Refresh failed or the family was revoked — drop to the login screen.
      setAuthToken(null);
      setRefreshToken(null);
      setUser(null);
      tokenStore.remove(TOKEN_KEY).catch(() => {});
      tokenStore.remove(REFRESH_KEY).catch(() => {});
    });
    return () => {
      setTokenRotationHandler(null);
      setSessionLostHandler(null);
    };
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
      signInWithSocial,
      socialProviders,
    }),
    [
      booting,
      user,
      mode,
      canWork,
      login,
      register,
      logout,
      refreshMe,
      setMode,
      signInWithSocial,
      socialProviders,
    ]
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
