import { useState } from "react";
import { Link } from "react-router-dom";
import { LiveImage } from "../components/LiveImage";
import type { Alert, Camera, NvrWatch, RecorderHealth } from "../api/types";
import { isIntegrator } from "../auth/AuthContext";
import { badSectors, diskVerdict, fmtAge } from "../components/DiskHealth";
import { DiskDiagnostic } from "../components/NvrConfig";
import { siteState, useCurrent } from "../components/SiteContext";
import { Empty, ErrorState, Loading, Modal, Notice, PageHeader } from "../components/ui";
import { useCamAdmin } from "./Cameras";
import { ago, fmtDateTime } from "../format";

export const KIND: Record<string, string> = {
  nvr: "Enregistreur injoignable", rec: "N'enregistre plus", camera: "Caméra déconnectée", hdd: "Disque à remplacer",
  offline: "Équipement injoignable",
};

type Tone = "ok" | "warn" | "bad" | "wait";

/** État d'une tuile du mur : ce que le NVR dit de la caméra (pas d'image en direct : refusé par l'utilisateur). */
export function camTile(c: Camera, alerts: Alert[], last: string | null | undefined, nvrDown: boolean):
  { tone: Tone; status: string; title: string; note: string } {
  const rec = alerts.find((a) => a.camera_id === c.id && a.kind === "rec");
  if (nvrDown) return { tone: "bad", status: "INCONNU", title: "AUCUN SIGNAL", note: "Enregistreur injoignable : état de la caméra inconnu" };
  if (c.online === 0) return { tone: "bad", status: "DÉCONNECTÉE", title: "AUCUN SIGNAL", note: rec ? rec.message.replace(/^.*?: /, "") : "Le NVR ne reçoit plus ses images" };
  if (rec) return { tone: "warn", status: "N'ENREGISTRE PLUS", title: "VUE PAR LE NVR", note: rec.message.replace(/^.*?: /, "") };
  if (c.online == null) return { tone: "wait", status: "VÉRIFICATION", title: "PREMIER RELEVÉ", note: "Dans les secondes qui viennent" };
  return { tone: "ok", status: "EN LIGNE", title: "ENREGISTRE", note: last ? `Dernière image enregistrée : ${last.slice(11, 16)} (heure du NVR)` : "Vue par le NVR" };
}

/** Tuile du mur : image en direct si le NVR répond (sinon l'état seul), état et boutons par-dessus. */
function WallTile({ c, t, actions, onZoom }: { c: Camera; t: ReturnType<typeof camTile>; actions: React.ReactNode; onZoom: () => void }) {
  const [liveState, setLiveState] = useState<{ s: "attente" | "ok" | "erreur"; msg?: string }>({ s: "attente" });
  const showLive = t.status !== "INCONNU" && c.brand === "dahua" && !!c.enabled;
  const playing = showLive && liveState.s === "ok";
  return (
    <div className={`wtile tone-${t.tone} ${playing ? "wtile-playing" : ""}`}>
      {showLive && <button type="button" className="wtile-media" onClick={onZoom} aria-label={`Agrandir ${c.name}`}>
        <LiveImage camId={c.id} onState={(s, msg) => setLiveState((o) => (o.s === s && o.msg === msg ? o : { s, msg }))} /></button>}
      <div className="wtile-top"><span className="mono wtile-name">{c.name}</span>
        <span className="wtile-badge">{playing && t.tone === "ok" ? "● DIRECT" : t.status}</span></div>
      <div className="wtile-bottom">
        {!playing && <><div className="mono wtile-title">{showLive && liveState.s === "attente" ? "CONNEXION AU DIRECT…" : t.title}</div>
          <div className="wtile-note">{showLive && liveState.s === "erreur" ? `Direct indisponible : ${liveState.msg}` : t.note}</div></>}
        {playing && t.tone !== "ok" && <div className="wtile-note">{t.note}</div>}
        {actions}
      </div>
    </div>
  );
}

const STATE: Record<string, { label: string; tone: Tone }> = {
  off: { label: "HORS LIGNE", tone: "bad" }, warn: { label: "ALERTE", tone: "warn" }, ok: { label: "EN LIGNE", tone: "ok" },
  attente: { label: "VÉRIFICATION…", tone: "wait" }, vide: { label: "SANS CAMÉRA", tone: "wait" },
};

function DiskAside({ recorders, admin }: { recorders: RecorderHealth[]; admin: boolean }) {
  const disks = recorders.flatMap((r) => r.disks);
  const unreadable = recorders.filter((r) => r.error && !r.disks.length);
  return (
    <section className="dash-card">
      <div className="dash-head"><h2>Disques durs</h2>
        {admin && [...new Set(recorders.filter((r) => r.brand === "dahua").map((r) => r.host))].map((h) => <DiskDiagnostic key={h} host={h} />)}</div>
      {!recorders.length && <p className="dash-text muted">Premier relevé dans les 30 secondes qui suivent le démarrage.</p>}
      {unreadable.map((r) => <p key={r.host} className="dash-text">Illisibles : le NVR {r.host} ne répond pas, impossible de lire l’état SMART
        (secteurs défectueux, température, usure).</p>)}
      {disks.map((d) => {
        const v = diskVerdict(d), sectors = badSectors(d);
        return (
          <div key={d.name} className={`dash-disk tone-${v.tone}`}>
            <div className="dash-disk-verdict">{v.tone === "ok" ? "✔" : "⚠"} {v.title}</div>
            <div className="mono small muted">{d.model || d.name} · {d.name}</div>
            <div className="small">{v.text}</div>
            <div className="dash-figures">
              <span><b>{sectors ?? "—"}</b> secteur{sectors === 1 ? "" : "s"} défectueux</span>
              <span><b>{d.temp != null ? `${d.temp} °C` : "—"}</b></span>
              <span><b>{d.hours == null ? "—" : fmtAge(d.hours)}</b> de marche</span>
            </div>
          </div>
        );
      })}
    </section>
  );
}

/** Tableau de bord (maquette « Option B — Mur de caméras ») : état du site, mur des caméras, alertes, disques. */
export default function Dashboard() {
  const { ov: d, error, loading, reload, name, site, cams: all, alerts, recorders, nvr } = useCurrent();
  const ca = useCamAdmin();                    // gestion des NVR et caméras (installateur), ex-page « NVR et disques »
  const [zoom, setZoom] = useState<Camera | null>(null);
  if (loading && !d) return <Loading />;
  if (error && !d) return <ErrorState message={error} onRetry={reload} />;
  if (!d) return null;
  const admin = isIntegrator(d.me);
  if (!d.sites.length) return <><PageHeader title="Tableau de bord">{admin && ca.toolbar}</PageHeader>
    <Empty>Aucune pharmacie{admin ? " : cliquez sur « Ajouter un enregistreur »." : "."}</Empty>{admin && ca.modals}</>;
  const cams = all.filter((c) => c.enabled).sort((a, b) => a.channel - b.channel || a.name.localeCompare(b.name, "fr", { numeric: true }));
  const offline = cams.filter((c) => c.online === 0).length;
  const seen = cams.filter((c) => c.online === 1).length;
  const notRecording = new Set(alerts.filter((a) => a.kind === "rec").map((a) => a.camera_id)).size;
  const disks = recorders.flatMap((r) => r.disks);
  const badDisks = disks.filter((x) => x.level === "bad").length;
  const unreadable = recorders.some((r) => r.error && !r.disks.length);
  const state = STATE[siteState(d, site)] ?? STATE.attente;
  const down = new Map(nvr.filter((n) => n.error).map((n) => [n.host, n] as [string, NvrWatch]));
  const nvrAlert = alerts.find((a) => a.kind === "nvr");
  const hosts = [...new Set(all.map((c) => c.host))];
  const lastOf = (c: Camera) => nvr.find((n) => n.host === c.host)?.cameras.find((x) => x.id === c.id)?.last_record;
  const problems = alerts.length;
  return (
    <div className="dash">
      {admin && <div className="row dash-toolbar">{ca.toolbar}</div>}
      {!d.email && admin && <Notice tone="warn">E-mail non configuré : les alertes ne s’affichent que sur ce poste (voir Paramètres).</Notice>}
      <section className="dash-band">
        <Link to="/alertes" className={`dash-state dash-state-link tone-${state.tone}`}
              title={problems ? `Voir les ${problems} alerte(s) en cours` : "Voir les alertes"}>
          <div className="dash-site">SITE · {name.toUpperCase()}</div>
          <div className="dash-state-label">{state.label}</div>
          <div className="dash-state-text">
            {state.tone === "bad" ? <>Le NVR ne répond plus{nvrAlert ? <> depuis <b className="mono">{ago(nvrAlert.opened_ts)}</b></> : null}. Éteint, câble ou réseau coupé ?</>
              : state.tone === "warn" ? <>{problems} problème{problems > 1 ? "s" : ""} en cours : <u>voir les alertes ›</u></>
              : state.tone === "ok" ? <>Toutes les caméras sont vues par le NVR et enregistrent.</>
              : <>Premier relevé du NVR en cours.</>}
          </div>
          {problems > 0 && state.tone !== "warn" && <div className="dash-state-more">{problems} alerte{problems > 1 ? "s" : ""} en cours ›</div>}
        </Link>
        <div className="dash-counters">
          <div className="dash-counter"><div className={`dash-num ${seen < cams.length ? "bad" : ""}`}>{seen}<span>/{cams.length}</span></div><div className="dash-cap">vues par le NVR</div></div>
          <div className="dash-counter"><div className={`dash-num ${offline ? "bad" : ""}`}>{offline}</div><div className="dash-cap">déconnectées</div></div>
          <div className="dash-counter"><div className={`dash-num ${notRecording ? "bad" : ""}`}>{notRecording}</div><div className="dash-cap">non enregistrées depuis plus de 15 min</div></div>
          <div className="dash-counter"><div className={`dash-num ${badDisks ? "bad" : ""}`}>{badDisks}</div><div className="dash-cap">disques à remplacer{unreadable ? " (état illisible)" : ""}</div></div>
        </div>
      </section>

      <div className="dash-cols">
        <section className="dash-main">
          <div className="dash-head"><h2>Mur de caméras</h2><span className="mono small muted">Mis à jour {fmtDateTime(d.now)}</span>
            <button className="btn small" onClick={reload}>Actualiser</button></div>
          <div className="wall">
            {cams.map((c) => (
              <WallTile key={c.id} c={c} t={camTile(c, alerts, lastOf(c), down.has(c.host))}
                        actions={admin ? ca.camActions(c) : null} onZoom={() => setZoom(c)} />
            ))}
            {hosts.map((h) => {
              const n = nvr.find((x) => x.host === h);
              const bad = !!n?.error;
              return (
                <div key={h} className={`wtile wtile-nvr tone-${bad ? "bad" : n ? "ok" : "wait"}`}>
                  <div className="wtile-top"><span className="mono wtile-name">NVR</span><span className="wtile-badge">{bad ? "INJOIGNABLE" : n ? "JOIGNABLE" : "VÉRIFICATION"}</span></div>
                  <div><div className="mono wtile-host">{h}</div>
                    <div className="wtile-note">{bad ? (nvrAlert ? `Ne répond plus depuis ${ago(nvrAlert.opened_ts)}` : n?.error) : n ? `Relevé il y a ${ago(n.checked)}` : "Premier relevé en cours"}</div>
                    {bad && n?.error && <div className="wtile-note">{n.error}{admin && n.detail ? <> · <code title="Détail technique, pour l’assistance">{n.detail}</code></> : null}</div>}
                    {admin && (() => { const c = all.find((x) => x.host === h); return c ? ca.nvrActions(c) : null; })()}</div>
                </div>
              );
            })}
          </div>
        </section>

        <aside className="dash-side">
          <section className="dash-card">
            <div className="dash-head"><h2>Alertes en cours</h2><Link to="/alertes" className="small">Tout voir</Link></div>
            {alerts.length === 0 ? <p className="dash-text muted">Aucune alerte : tout fonctionne.</p> : alerts.slice(0, 6).map((a) => (
              <div key={a.id} className="dash-alert">
                <div className="dash-alert-top"><b>{KIND[a.kind] ?? a.kind}</b><span className="mono small dash-since">{ago(a.opened_ts)}</span></div>
                <div className="small muted">{a.message}</div>
              </div>
            ))}
            {alerts.length > 6 && <p className="small muted">… et {alerts.length - 6} autre(s).</p>}
          </section>
          <DiskAside recorders={recorders} admin={admin} />
          {admin && ca.modals}
          {zoom && (
            <Modal wide title={`${zoom.name} · en direct`} onClose={() => setZoom(null)}>
              <img className="live-zoom" src={`/api/cameras/${zoom.id}/live`} alt={`${zoom.name} en direct`} />
              <p className="muted small">Flux secondaire du NVR (petite résolution, quelques images par seconde). Pour revoir
                un moment ou l’extraire en qualité d’origine : onglet <Link to="/video">Back-up</Link>.</p>
            </Modal>
          )}
        </aside>
      </div>
    </div>
  );
}
