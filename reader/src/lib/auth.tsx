"use client";

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import { api, getToken, setToken, syncLocalToServer } from "./api";
import type { ReaderUser } from "./types";

type AuthCtx = {
  user: ReaderUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, nickname?: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<ReaderUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await api.get<ReaderUser>("/reader/auth/me");
      setUser(me);
    } catch {
      setToken(null);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function login(username: string, password: string) {
    const res = await api.post<{ access_token: string; user: ReaderUser }>("/reader/auth/login", {
      username,
      password,
    });
    setToken(res.access_token);
    setUser(res.user);
    try {
      await syncLocalToServer();
    } catch {
      /* ignore merge errors */
    }
  }

  async function register(username: string, password: string, nickname?: string) {
    const res = await api.post<{ access_token: string; user: ReaderUser }>("/reader/auth/register", {
      username,
      password,
      nickname: nickname || null,
    });
    setToken(res.access_token);
    setUser(res.user);
    try {
      await syncLocalToServer();
    } catch {
      /* ignore */
    }
  }

  function logout() {
    setToken(null);
    setUser(null);
  }

  return (
    <Ctx.Provider value={{ user, loading, login, register, logout, refresh }}>{children}</Ctx.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
