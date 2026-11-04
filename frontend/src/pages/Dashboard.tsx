import { Link } from "react-router-dom";
import type { Alert, Camera, NvrWatch, RecorderHealth } from "../api/types";
import { isIntegrator } from "../auth/AuthContext";
import { badSectors, diskVerdict, fmtAge } from "../components/DiskHealth";
import { DiskDiagnostic, NvrConfig } from "../components/NvrConfig";
import { siteState, useCurrent } from "../components/SiteContext";
import { Empty, ErrorState, Loading, Notice, PageHeader } from "../components/ui";
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
  if (loading && !d) return <Loading />;
  if (error && !d) return <ErrorState message={error} onRetry={reload} />;
  if (!d) return null;
  if (!d.sites.length) return <><PageHeader title="Tableau de bord" /><Empty>Aucune pharmacie : ajoutez un enregistreur dans « NVR et disques ».</Empty></>;
  const admin = isIntegrator(d.me);
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
      {!d.email && admin && <Notice tone="warn">E-mail non configuré : les alertes ne s’affichent que sur ce poste (voir Paramètres).</Notice>}
      <section className="dash-band">
        <div className={`dash-state tone-${state.tone}`}>
          <div className="dash-site">SITE · {name.toUpperCase()}</div>
          <div className="dash-state-label">{state.label}</div>
          <div className="dash-state-text">
            {state.tone === "bad" ? <>Le NVR ne répond plus{nvrAlert ? <> depuis <b className="mono">{ago(nvrAlert.opened_ts)}</b></> : null}. Éteint, câble ou réseau coupé ?</>
              : state.tone === "warn" ? <>{problems} problème{problems > 1 ? "s" : ""} en cours : voir les alertes.</>
              : state.tone === "ok" ? <>Toutes les caméras sont vues par le NVR et enregistrent.</>
              : <>Premier relevé du NVR en cours.</>}
          </div>
        </div>
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
            {cams.map((c) => {
              const t = camTile(c, alerts, lastOf(c), down.has(c.host));
              return (
                <div key={c.id} className={`wtile tone-${t.tone}`}>
                  <div className="wtile-top"><span className="mono wtile-name">{c.name}</span><span className="wtile-badge">{t.status}</span></div>
                  <div><div className="mono wtile-title">{t.title}</div><div className="wtile-note">{t.note}</div></div>
                </div>
              );
            })}
            {hosts.map((h) => {
              const n = nvr.find((x) => x.host === h);
              const bad = !!n?.error;
              return (
                <div key={h} className={`wtile wtile-nvr tone-${bad ? "bad" : n ? "ok" : "wait"}`}>
                  <div className="wtile-top"><span className="mono wtile-name">NVR</span><span className="wtile-badge">{bad ? "INJOIGNABLE" : n ? "JOIGNABLE" : "VÉRIFICATION"}</span></div>
                  <div><div className="mono wtile-host">{h}</div>
                    <div className="wtile-note">{bad ? (nvrAlert ? `Ne répond plus depuis ${ago(nvrAlert.opened_ts)}` : n?.error) : n ? `Relevé il y a ${ago(n.checked)}` : "Premier relevé en cours"}</div></div>
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
          {admin && hosts.map((h) => (
            <section key={h} className="dash-card">
              <div className="dash-head"><h2>Configuration NVR{hosts.length > 1 ? ` ${h}` : ""}</h2></div>
              <NvrConfig host={h} site={name} />
            </section>
          ))}
        </aside>
      </div>
    </div>
  );
}
