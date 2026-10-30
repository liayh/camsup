import { FormEvent, useState } from "react";
import { del, post, put } from "../api/client";
import type { Site } from "../api/types";
import { isIntegrator, useAuth } from "../auth/AuthContext";
import { useAction } from "../components/Toast";
import { Empty, ErrorState, Loading, Modal, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";

export default function Sites() {
  const { me } = useAuth();
  const admin = isIntegrator(me);
  const sites = useApi<Site[]>("/sites");
  const run = useAction();
  const [edit, setEdit] = useState<Site | "new" | null>(null);

  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = { name: String(f.get("name")), notes: String(f.get("notes") || "") };
    const ok = await run(() => (edit === "new" ? post("/sites", body) : put(`/sites/${(edit as Site).id}`, body)), "Site enregistré");
    if (ok) { setEdit(null); sites.reload(); }
  }
  async function remove(s: Site) {
    if (!confirm(`Supprimer le site « ${s.name} » ?`)) return;
    if (await run(() => del(`/sites/${s.id}`), "Site supprimé")) sites.reload();
  }

  if (sites.loading && !sites.data) return <Loading />;
  if (sites.error && !sites.data) return <ErrorState message={sites.error} onRetry={sites.reload} />;
  const rows = sites.data ?? [];
  return (
    <>
      <PageHeader title="Sites">
        {admin && <button className="btn primary" onClick={() => setEdit("new")}>Nouveau site</button>}
      </PageHeader>
      {!rows.length ? <Empty>Aucun site.</Empty> : (
        <table className="card">
          <thead><tr><th>Site</th><th>Caméras</th><th>Notes</th>{admin && <th />}</tr></thead>
          <tbody>{rows.map((s) => (
            <tr key={s.id}><td><b>{s.name}</b></td><td>{s.cameras}</td><td className="muted">{s.notes}</td>
              {admin && <td className="right"><button className="btn small" onClick={() => setEdit(s)}>Modifier</button>
                <button className="btn small danger" onClick={() => remove(s)}>Supprimer</button></td>}</tr>
          ))}</tbody>
        </table>
      )}
      {edit && (
        <Modal title={edit === "new" ? "Nouveau site" : `Modifier « ${edit.name} »`} onClose={() => setEdit(null)}>
          <form className="form" onSubmit={save}>
            <label>Nom<input name="name" required maxLength={120} defaultValue={edit === "new" ? "" : edit.name} /></label>
            <label>Notes<input name="notes" defaultValue={edit === "new" ? "" : edit.notes} /></label>
            <button className="btn primary">Enregistrer</button>
          </form>
        </Modal>
      )}
    </>
  );
}
