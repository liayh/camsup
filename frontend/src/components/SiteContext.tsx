import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import type { Overview } from "../api/types";
import { useApi } from "../hooks/useApi";

/** Pharmacie affichée (colonne de gauche) et état général, partagés par toutes les pages : un seul relevé toutes les 5 s. */
type Ctx = {
  site: number; setSite: (id: number) => void;
  ov: Overview | null; error: string | null; loading: boolean; reload: () => void;
};
const SiteCtx = createContext<Ctx | null>(null);
const KEY = "camsup.site";

export type SiteTone = "ok" | "warn" | "off" | "vide" | "attente";
export const SITE_LABEL: Record<SiteTone, string> = { ok: "En ligne", warn: "Alerte", off: "Hors ligne", vide: "Sans caméra", attente: "Vérification…" };

/** État d'une pharmacie : NVR injoignable ou aucune caméra vue → hors ligne ; une alerte ou une caméra perdue → alerte. */
export function siteState(ov: Overview, site: number): SiteTone {
  const cams = ov.cameras.filter((c) => c.site_id === site && c.enabled);
  if (!cams.length) return "vide";
  if (ov.nvr.some((n) => n.site_id === site && n.error) || cams.every((c) => c.online === 0)) return "off";
  if (ov.alerts.some((a) => a.site_id === site) || cams.some((c) => c.online === 0)) return "warn";
  return cams.every((c) => c.online == null) ? "attente" : "ok";      // premier relevé pas encore fait
}

export function SiteProvider({ children }: { children: ReactNode }) {
  const { data: ov, error, loading, reload } = useApi<Overview>("/overview", 5000);
  const [chosen, setChosen] = useState<number>(() => Number(localStorage.getItem(KEY)) || 0);
  const ids = ov?.sites.map((s) => s.id) ?? [];
  const site = ids.includes(chosen) ? chosen : ids[0] ?? 0;
  useEffect(() => { if (site) localStorage.setItem(KEY, String(site)); }, [site]);
  return <SiteCtx.Provider value={{ site, setSite: setChosen, ov, error, loading, reload }}>{children}</SiteCtx.Provider>;
}

export function useSite(): Ctx {
  const c = useContext(SiteCtx);
  if (!c) throw new Error("useSite hors de SiteProvider");
  return c;
}

/** Ce qui concerne la pharmacie affichée. */
export function useCurrent() {
  const ctx = useSite();
  const { ov, site } = ctx;
  return {
    ...ctx,
    name: ov?.sites.find((s) => s.id === site)?.name ?? "",
    cams: ov?.cameras.filter((c) => c.site_id === site) ?? [],
    alerts: ov?.alerts.filter((a) => a.site_id === site) ?? [],
    recorders: ov?.recorders.filter((r) => r.site_id === site) ?? [],
    nvr: ov?.nvr.filter((n) => n.site_id === site) ?? [],
  };
}
