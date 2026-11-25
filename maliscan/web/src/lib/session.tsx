"use client";

import { Session } from "@supabase/supabase-js";
import { usePathname, useRouter } from "next/navigation";
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, wakeApi } from "./api";
import { supabase } from "./supabase";
import type { Me, Membership, Role } from "./types";

interface Ctx {
  session: Session | null;
  me: Me | null;
  loading: boolean;
  firm: Membership | null;
  setFirmId: (id: string) => void;
  refreshMe: () => Promise<void>;
  signOut: () => Promise<void>;
  can: (...roles: Role[]) => boolean;
}

const SessionContext = createContext<Ctx | null>(null);
const FIRM_KEY = "maliscan.firm";
const PUBLIC = ["/login", "/register", "/forgot-password", "/reset-password", "/invite", "/indir"];

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [firmId, setFirmIdState] = useState<string | null>(null);
  const router = useRouter();
  const pathname = usePathname();

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api<Me>("/me"));
    } catch {
      setMe(null);
    }
  }, []);

  useEffect(() => {
    wakeApi();
    let mounted = true;
    try { setFirmIdState(localStorage.getItem(FIRM_KEY)); } catch { /* private mode */ }
    supabase().auth.getSession().then(async ({ data }) => {
      if (!mounted) return;
      setSession(data.session);
      if (data.session) await refreshMe();
      setLoading(false);
    });
    // Never await Supabase calls inside this callback: supabase-js holds its auth
    // lock while invoking it, and getSession() (used by api()) would deadlock.
    const { data: sub } = supabase().auth.onAuthStateChange((_event, s) => {
      setSession(s);
      if (s) setTimeout(() => void refreshMe(), 0);
      else setMe(null);
    });
    return () => { mounted = false; sub.subscription.unsubscribe(); };
  }, [refreshMe]);

  const isPublic = PUBLIC.some((p) => pathname?.startsWith(p));
  useEffect(() => {
    if (loading) return;
    if (!session && !isPublic && pathname !== "/") router.replace(`/login?next=${encodeURIComponent(pathname || "/")}`);
    if (session && me && !isPublic && me.memberships.length === 0 && !pathname?.startsWith("/onboarding")
        && !pathname?.startsWith("/admin")) router.replace("/onboarding");
  }, [loading, session, me, isPublic, pathname, router]);

  const firm = useMemo(() => {
    if (!me?.memberships.length) return null;
    return me.memberships.find((m) => m.firm_id === firmId) ?? me.memberships[0];
  }, [me, firmId]);

  const value: Ctx = {
    session, me, loading, firm,
    setFirmId: (id) => { setFirmIdState(id); try { localStorage.setItem(FIRM_KEY, id); } catch { /* ignore */ } },
    refreshMe,
    signOut: async () => { await supabase().auth.signOut(); setMe(null); router.replace("/login"); },
    can: (...roles) => !!firm && roles.includes(firm.role),
  };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Ctx {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside provider");
  return ctx;
}

export const BOOKERS: Role[] = ["owner", "admin", "accountant"];
export const WRITERS: Role[] = ["owner", "admin", "accountant", "clerk"];
export const MANAGERS: Role[] = ["owner", "admin"];
