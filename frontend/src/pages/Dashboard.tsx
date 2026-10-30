import type { Overview } from "../api/types";
import { DiskHealth } from "../components/DiskHealth";
import { Badge, Empty, ErrorState, Loading, Notice, PageHeader, Stat } from "../components/ui";
import { ago, fmtDateTime } from "../format";
import { useApi } from "../hooks/useApi";

const KIND: Record<string, string> = {
  nvr: "Enregistreur injoignable", rec: "N'enregistre plus", camera: "Caméra déconnectée", hdd: "Disque à remplacer",
  offline: "Équipement injoignable",
};

export default function Dashboard() {
  const { data: d, error, loading, reload } = useApi<Overview>("/overview", 5000);
  if (loading && !d) return <Loading />;
  if (error && !d) return <ErrorState message={error} onRetry={reload} />;
  if (!d) return null;
  const cams = d.cameras.filter((c) => c.enabled);
  const unchecked = cams.filter((c) => c.online == null).length;
  const offline = cams.filter((c) => c.online === 0).length;
  const notRecording = new Set(d.alerts.filter((a) => a.kind === "rec").map((a) => a.camera_id)).size;
  const badDisks = d.recorders.flatMap((r) => r.disks).filter((x) => x.level === "bad").length;
  const watchDisks = d.recorders.flatMap((r) => r.disks).filter((x) => x.level === "warn").length;
  return (
    <>
      <PageHeader title="État de la vidéosurveillance">
        <span className="muted small">Mis à jour {fmtDateTime(d.now)}</span>
        <button className="btn small" onClick={reload}>Actualiser</button>
      </PageHeader>
      {!d.email && d.me.role === "integrator" &&
        <Notice tone="warn">E-mail non configuré : les alertes ne s’affichent que sur ce poste (voir Paramètres).</Notice>}
      <div className="stats">
        <Stat label="Caméras vues par le NVR" value={cams.length - offline - unchecked} tone="ok"
              hint={unchecked ? `${unchecked} pas encore vérifiée(s)` : `sur ${cams.length}`} />
        <Stat label="Caméras déconnectées" value={offline} tone={offline ? "bad" : "ok"} />
        <Stat label="Caméras non enregistrées" value={notRecording} tone={notRecording ? "bad" : "ok"} hint="depuis plus de 15 min" />
        <Stat label="Disques à remplacer" value={badDisks} tone={badDisks ? "bad" : "ok"}
              hint={watchDisks ? `${watchDisks} à surveiller` : undefined} />
      </div>
      <section className="card">
        <h2>Alertes en cours</h2>
        {d.alerts.length === 0 ? <Empty>Aucune alerte : tout fonctionne.</Empty> : (
          <table><tbody>{d.alerts.map((a) => (
            <tr key={a.id}><td><Badge tone="bad">{KIND[a.kind] ?? a.kind}</Badge></td><td>{a.message}</td>
              <td className="muted">depuis {ago(a.opened_ts)}</td></tr>))}</tbody></table>
        )}
      </section>
      <section className="card">
        <h2>Disques durs des enregistreurs</h2>
        {d.recorders.length === 0 ? <Empty>Premier relevé dans les 30 secondes qui suivent le démarrage.</Empty>
          : d.recorders.map((r) => (
            <div key={r.host}>
              <div className="muted small">{d.sites.find((s) => s.id === r.site_id)?.name} · enregistreur {r.host} · relevé il y a {ago(r.last_check)}</div>
              {r.error && !r.disks.length && <div className="error-text">Disques : {r.error}</div>}
              {r.disks.map((x) => <DiskHealth key={x.name} d={x} />)}
            </div>
          ))}
      </section>
    </>
  );
}
