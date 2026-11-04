import { useState } from "react";
import { fmtBytes, fmtDateTime } from "../format";
import { fmtAge } from "./DiskHealth";
import { Badge, Modal } from "./ui";
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

type SmartRow = { id: number; nom: string; valeur: number | null; actuel: number; pire: number; seuil: number; etat: "bon" | "surveiller" | "defaillant" };
type DiagDisk = { nom: string; modele: string; serie: string; etat: string; taille: number; libre: number; reel: boolean;
  partitions: { nom: string; taille: number; libre: number; etat: string }[]; smart: SmartRow[] };
type Diag = { host: string; date: string; erreur?: string | null; resume: { modele: string; version: string; disques: DiagDisk[] } | null };

const VERDICT = { bon: ["ok", "Bon"], surveiller: ["warn", "À surveiller"], defaillant: ["bad", "Défaillant"] } as const;
/** Valeur d'un compteur, dite en clair. */
function shown(r: SmartRow): string {
  if (r.valeur == null) return "—";
  if (r.id === 9) return fmtAge(r.valeur);
  if (r.id === 190 || r.id === 194) return `${r.valeur} °C`;
  return Number(r.valeur).toLocaleString("fr-FR");
}
/** Verdict global d'un disque d'après ses compteurs. */
export function diskOverall(d: { etat: string; smart: { etat: string }[] }): "bon" | "surveiller" | "defaillant" {
  if (d.smart.some((x) => x.etat === "defaillant") || /error|fail/i.test(d.etat)) return "defaillant";
  return d.smart.some((x) => x.etat === "surveiller") ? "surveiller" : "bon";
}

/** Diagnostic des disques (intégrateur) : fenêtre en clair, fichier technique à télécharger pour correction. */
export function DiskDiagnostic({ host }: { host: string }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Diag | null>(null);
  const [error, setError] = useState("");
  const show = async () => {
    setOpen(true); setData(null); setError("");
    try {
      const r = await fetch(`/api/nvr/disks/diagnostic?host=${encodeURIComponent(host)}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `erreur ${r.status}`);
      setData(await r.json());
    } catch (e) { setError((e as Error).message); }
  };
  const file = async () => {
    try {
      const r = await fetch(`/api/nvr/disks/diagnostic?host=${encodeURIComponent(host)}&fichier=1`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(`erreur ${r.status}`);
      const name = /filename="([^"]+)"/.exec(r.headers.get("content-disposition") ?? "")?.[1] ?? "Diagnostic_disques.json";
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a"); a.href = url; a.download = name; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      toast(`Fichier technique enregistré : ${name} (dossier Téléchargements)`, "ok");
    } catch (e) { toast(`Fichier non enregistré : ${(e as Error).message}`, "bad"); }
  };
  const disks = data?.resume?.disques ?? [];
  return (
    <>
      <button className="btn small" onClick={show}>Diagnostic des disques</button>
      {open && (
        <Modal title={`Diagnostic des disques · NVR ${host}`} onClose={() => setOpen(false)} wide>
          {!data && !error && <p className="muted"><span className="spinner" /> Lecture du NVR…</p>}
          {error && <p className="error-text">Diagnostic impossible : {error}</p>}
          {data && !data.resume && <p className="error-text">Le NVR n’a pas répondu au diagnostic{data.erreur ? ` : ${data.erreur}` : ""}.</p>}
          {data?.resume && (
            <>
              <p className="muted small">Enregistreur {data.resume.modele || "modèle inconnu"}{data.resume.version ? ` · version ${data.resume.version}` : ""}
                {" "}· lu le {data.date} · {disks.filter((d) => d.reel).length} disque(s)</p>
              {disks.map((d) => {
                if (!d.reel) return <p key={d.nom} className="muted small">{d.nom} : emplacement vide (pas de disque), ignoré.</p>;
                const v = diskOverall(d);
                const [tone] = VERDICT[v];
                const parts = d.partitions.length;
                return (
                  <section key={d.nom} className={`db-tile db-${tone}`} style={{ marginBottom: 12 }}>
                    <div className="db-verdict">{v === "bon" ? "✔" : "⚠"} {v === "bon" ? "Disque en bon état" : v === "surveiller" ? "Disque à surveiller" : "Disque défaillant : à remplacer"}</div>
                    <div className="db-name">{d.modele || "modèle inconnu"} · n° {d.serie || "—"} · {d.nom}</div>
                    <ul className="small" style={{ margin: "8px 0", paddingLeft: 18 }}>
                      <li><b>Capacité : {fmtBytes(d.taille)}</b>{parts > 1 ? <> — le NVR l’a découpée en {parts} partitions de {fmtBytes(d.partitions[0].taille)} (c’est normal)</> : null}</li>
                      <li>Espace libre : {d.libre === 0 ? <>aucun — <b>normal</b> : le NVR efface les plus anciennes vidéos pour enregistrer les nouvelles</> : fmtBytes(d.libre)}</li>
                      <li>État donné par le NVR : {/success|running/i.test(d.etat) ? "en service" : d.etat || "—"}</li>
                    </ul>
                    {(() => { const h = d.smart.find((x) => x.id === 9)?.valeur; return h != null && h >= 5 * 8766
                      ? <p className="small" style={{ color: "var(--warn)" }}>⚠ {fmtAge(h)} de marche : au-delà de la durée de vie habituelle
                          (3 à 5 ans). Même sans secteur abîmé, prévoyez son remplacement par précaution.</p> : null; })()}
                    {d.smart.length === 0 ? <p className="muted small">Ce NVR ne donne pas les compteurs de santé (SMART) de ce disque.</p> : (
                      <table className="small"><thead><tr><th>Compteur de santé</th><th>Valeur</th><th>Verdict</th></tr></thead>
                        <tbody>{d.smart.map((r) => {
                          const [t, l] = VERDICT[r.etat];
                          return <tr key={r.id}><td>{r.nom}</td><td>{shown(r)}</td><td><Badge tone={t}>{l}</Badge></td></tr>;
                        })}</tbody></table>
                    )}
                  </section>
                );
              })}
              <p className="muted small">Les compteurs importants : <b>secteurs réalloués, en attente, illisibles</b>. À 0, le
                disque va bien ; s’ils augmentent d’un relevé à l’autre, il faut le remplacer. Un disque de vidéosurveillance dure
                en général 3 à 5 ans en marche continue.</p>
            </>
          )}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn small" onClick={file}>Télécharger le fichier technique (pour l’assistance)</button>
            <span className="grow" />
            <button className="btn primary" onClick={() => setOpen(false)}>Fermer</button>
          </div>
        </Modal>
      )}
    </>
  );
}
