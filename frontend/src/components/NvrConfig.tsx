import { useState } from "react";
import { fmtDateTime } from "../format";
import { useSite } from "./SiteContext";
import { useToast } from "./Toast";

/** Sauvegarde de la configuration d'un NVR (intégrateur) : fichier téléchargé, pour le réinstaller à l'identique. */
export function NvrConfig({ host, site }: { host: string; site: string }) {
  const { ov, reload } = useSite();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const last = ov?.config_saved[host];
  const save = async () => {
    setBusy(true);
    try {
      const r = await fetch(`/api/nvr/config?host=${encodeURIComponent(host)}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `erreur ${r.status}`);
      const name = /filename="([^"]+)"/.exec(r.headers.get("content-disposition") ?? "")?.[1] ?? "Config_NVR.backup";
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url; a.download = name; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      toast(`Configuration du NVR de ${site} enregistrée : ${name} (dossier Téléchargements)`, "ok");
      reload();
    } catch (e) {
      toast(`Configuration non récupérée : ${(e as Error).message}`, "bad");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <p className="muted small">Dernière sauvegarde de la configuration : {last ? fmtDateTime(last) : "jamais"}. Permet de
        réinstaller le NVR à l’identique. Le fichier contient les identifiants des caméras : à garder en lieu sûr.</p>
      <button className="btn" onClick={save} disabled={busy}>{busy ? <><span className="spinner" /> Lecture du NVR…</> : "Exporter la configuration"}</button>
    </>
  );
}
