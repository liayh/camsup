import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, AUTH_EXPIRED, post } from "../api/client";
import type { Me } from "../api/types";

interface Auth { me: Me | null; checking: boolean; login(u: string, p: string): Promise<void>; logout(): Promise<void> }
const Ctx = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    api<Me | null>("/session").then(setMe).catch(() => setMe(null)).finally(() => setChecking(false));
    const expired = () => setMe(null);
    window.addEventListener(AUTH_EXPIRED, expired);
    return () => window.removeEventListener(AUTH_EXPIRED, expired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setMe(await post<Me>("/login", { username, password }));
  }, []);
  const logout = useCallback(async () => {
    try { await post("/logout"); } catch (e) { if (!(e instanceof ApiError)) throw e; }
    setMe(null);
  }, []);
  return <Ctx.Provider value={{ me, checking, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): Auth {
  const a = useContext(Ctx);
  if (!a) throw new Error("useAuth hors de AuthProvider");
  return a;
}
export const isIntegrator = (me: Me | null) => me?.role === "integrator";
