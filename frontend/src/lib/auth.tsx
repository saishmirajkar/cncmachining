import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, session } from "./api";
import type { Me, Membership } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  membership: Membership | null;
  login: (email: string, password: string) => Promise<void>;
  register: (body: { email: string; password: string; full_name: string; organization_name: string }) => Promise<void>;
  logout: () => void;
  switchOrg: (orgId: string) => void;
  hasRole: (min: string) => boolean;
}

const RANK: Record<string, number> = { viewer: 0, operator: 1, programmer: 2, engineer: 3, admin: 4, owner: 5 };

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(Boolean(session.token));
  const [org, setOrg] = useState<string | null>(session.org);
  const qc = useQueryClient();

  const loadMe = useCallback(async () => {
    if (!session.token) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      const m = await api<Me>("/api/auth/me");
      setMe(m);
      const current = m.memberships.find((x) => x.organization_id === session.org) ?? m.memberships[0];
      session.org = current?.organization_id ?? null;
      setOrg(session.org);
    } catch {
      session.token = null;
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMe();
    const onLogout = () => {
      setMe(null);
      qc.clear();
    };
    window.addEventListener("cnc:logout", onLogout);
    return () => window.removeEventListener("cnc:logout", onLogout);
  }, [loadMe, qc]);

  const value = useMemo<AuthState>(() => {
    const membership = me?.memberships.find((m) => m.organization_id === org) ?? null;
    return {
      me,
      loading,
      membership,
      login: async (email, password) => {
        const r = await api<{ access_token: string }>("/api/auth/login", { method: "POST", json: { email, password } });
        session.token = r.access_token;
        setLoading(true);
        await loadMe();
      },
      register: async (body) => {
        const r = await api<{ access_token: string }>("/api/auth/register", { method: "POST", json: body });
        session.token = r.access_token;
        session.org = null;
        setLoading(true);
        await loadMe();
      },
      logout: () => {
        session.token = null;
        session.org = null;
        setMe(null);
        qc.clear();
      },
      switchOrg: (id) => {
        session.org = id;
        setOrg(id);
        qc.clear();
      },
      hasRole: (min) => (membership ? RANK[membership.role] >= RANK[min] : false),
    };
  }, [me, loading, org, loadMe, qc]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
