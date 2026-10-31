import { Link } from "react-router-dom";
import { isIntegrator } from "../auth/AuthContext";
import { DiskHealth } from "../components/DiskHealth";
import { NvrConfig } from "../components/NvrConfig";
import { useCurrent } from "../components/SiteContext";
import { Badge, Empty, ErrorState, Loading, Notice, PageHeader, Stat } from "../components/ui";
import { ago, fmtDateTime } from "../format";

export const KIND: Record<string, string> = {
  nvr: "Enregistreur injoignable", rec: "N'enregistre plus", camera: "Caméra déconnectée", hdd: "Disque à remplacer",
  offline: "Équipement injoignable",
};

/** Tableau de bord de la pharmacie choisie à gauche : caméras, alertes en cours, disques, enregistreur. */
export default function Dashboard() {
  const { ov: d, error, loading, reload, name, cams: all, alerts, recorders, nvr } = useCurrent();
  if (loading && !d) return <Loading />;
  if (error && !d) return <ErrorState message={error} onRetry={reload} />;
  if (!d) return null;
  if (!d.sites.length) return <><PageHeader title="Tableau de bord" /><Empty>Aucune pharmacie : ajoutez un enregistreur dans « NVR et disques ».</Empty></>;
  const cams = all.filter((c) => c.enabled);
  const unchecked = cams.filter((c) => c.online == null).length;
  const offline = cams.filter((c) => c.online === 0).length;
  const notRecording = new Set(alerts.filter((a) => a.kind === "rec").map((a) => a.camera_id)).size;
  const disks = recorders.flatMap((r) => r.disks);
  const badDisks = disks.filter((x) => x.level === "bad").length;
  const watchDisks = disks.filter((x) => x.level === "warn").length;
  const hosts = [...new Set(all.map((c) => c.host))];
  const admin = isIntegrator(d.me);
  return (
    <>
      <PageHeader title={name} sub={<>NVR {hosts.join(", ") || "—"} · {all.length} caméra{all.length > 1 ? "s" : ""}
        {nvr[0] ? <> · dernier relevé il y a {ago(nvr[0].checked)}</> : null}</>}>
        <span className="muted small">Mis à jour {fmtDateTime(d.now)}</span>
        <button className="btn small" onClick={reload}>Actualiser</button>
      </PageHeader>
      {!d.email && admin && <Notice tone="warn">E-mail non configuré : les alertes ne s’affichent que sur ce poste (voir Paramètres).</Notice>}
      <div className="split">
        <div className="split-main">
          <div className="stats">
            <Stat label="Caméras vues par le NVR" value={cams.length - offline - unchecked} tone="ok"
                  hint={unchecked ? `${unchecked} pas encore vérifiée(s)` : `sur ${cams.length}`} />
            <Stat label="Caméras déconnectées" value={offline} tone={offline ? "bad" : "ok"} />
            <Stat label="Caméras non enregistrées" value={notRecording} tone={notRecording ? "bad" : "ok"} hint="depuis plus de 15 min" />
            <Stat label="Disques à remplacer" value={badDisks} tone={badDisks ? "bad" : "ok"} hint={watchDisks ? `${watchDisks} à surveiller` : undefined} />
          </div>
          <section className="card">
            <div className="row"><h2>Alertes en cours</h2><span className="grow" /><Link to="/alertes" className="small">Voir toutes les alertes</Link></div>
            {alerts.length === 0 ? <Empty>Aucune alerte : tout fonctionne.</Empty> : (
              <table><tbody>{alerts.map((a) => (
                <tr key={a.id}><td><Badge tone="bad">{KIND[a.kind] ?? a.kind}</Badge></td><td>{a.message}</td>
                  <td className="muted">depuis {ago(a.opened_ts)}</td></tr>))}</tbody></table>
            )}
          </section>
          <section className="card">
            <h2>Disques durs de l’enregistreur</h2>
            {recorders.length === 0 ? <Empty>Premier relevé dans les 30 secondes qui suivent le démarrage.</Empty>
              : recorders.map((r) => (
                <div key={r.host}>
                  <div className="muted small">Enregistreur {r.host} · relevé il y a {ago(r.last_check)}</div>
                  {r.error && !r.disks.length && <div className="error-text">Disques : {r.error}</div>}
                  {r.disks.map((x) => <DiskHealth key={x.name} d={x} />)}
                </div>
              ))}
          </section>
        </div>
        <aside className="split-side">
          <section className="card">
            <h3>Santé du NVR</h3>
            {nvr.length === 0 ? <p className="muted small">Pas encore de relevé.</p> : nvr.map((n) => (
              <div key={n.host} className="kv">
                <span>{n.host}</span>{n.error ? <span className="pill pill-off">Injoignable</span> : <span className="pill pill-ok">Joignable</span>}
                {n.error && <span className="error-text small">{n.error}</span>}
              </div>
            ))}
            {disks.map((x) => (
              <div key={x.name} className="kv"><span>{x.name}</span>
                <span className={`pill pill-${x.level === "bad" ? "off" : x.level === "warn" ? "warn" : "ok"}`}>
                  {x.level === "bad" ? "À remplacer" : x.level === "warn" ? "À surveiller" : "Sain"}</span></div>
            ))}
          </section>
          {admin && hosts.map((h) => (
            <section key={h} className="card">
              <h3>Configuration NVR{hosts.length > 1 ? ` ${h}` : ""}</h3>
              <NvrConfig host={h} site={name} />
            </section>
          ))}
        </aside>
      </div>
    </>
  );
}
