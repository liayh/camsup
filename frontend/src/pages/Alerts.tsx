import { useState } from "react";
import type { Alert } from "../api/types";
import { useCurrent } from "../components/SiteContext";
import { Badge, Empty, ErrorState, Loading, PageHeader } from "../components/ui";
import { fmtDateTime, fmtDuration } from "../format";
import { useApi } from "../hooks/useApi";
import { KIND } from "./Dashboard";

/** Alertes de la pharmacie choisie : en cours et passées (rétablies), les plus récentes d'abord. */
export default function Alerts() {
  const { site, name } = useCurrent();
  const [closed, setClosed] = useState(true);
  const list = useApi<Alert[]>(site ? `/alerts?include_closed=${closed}&limit=500&site_id=${site}` : null, 10000);
  if (list.loading && !list.data) return <Loading />;
  if (list.error && !list.data) return <ErrorState message={list.error} onRetry={list.reload} />;
  const rows = list.data ?? [];
  return (
    <>
      <PageHeader title="Alertes" sub={name}>
        <label className="check small"><input type="checkbox" checked={closed} onChange={(e) => setClosed(e.target.checked)} /> Montrer les alertes rétablies</label>
      </PageHeader>
      <section className="card">
        {!rows.length ? <Empty>{closed ? "Aucune alerte pour cette pharmacie." : "Aucune alerte en cours."}</Empty> : (
          <table>
            <thead><tr><th>État</th><th>Type</th><th>Message</th><th>Début</th><th>Fin</th><th>Durée</th></tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id}>
                <td>{a.closed_ts ? <Badge tone="ok">rétablie</Badge> : <Badge tone="bad">en cours</Badge>}</td>
                <td>{KIND[a.kind] ?? a.kind}</td><td>{a.message}</td>
                <td className="mono">{fmtDateTime(a.opened_ts)}</td>
                <td className="mono">{a.closed_ts ? fmtDateTime(a.closed_ts) : "—"}</td>
                <td className="muted">{fmtDuration((a.closed_ts ?? Date.now() / 1000) - a.opened_ts)}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
      <p className="muted small">Fenêtre d’alerte sur le poste pour tout problème ; e-mail à l’installateur pour l’enregistreur
        injoignable, une caméra plus enregistrée et un disque à remplacer. Les 500 dernières alertes sont listées.</p>
    </>
  );
}
