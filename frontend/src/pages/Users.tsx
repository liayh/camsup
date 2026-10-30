import { FormEvent, useState } from "react";
import { del, post, put } from "../api/client";
import type { Role, Site, UserRow } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useAction } from "../components/Toast";
import { Badge, ErrorState, Loading, Modal, Notice, PageHeader } from "../components/ui";
import { fmtDateTime, ROLE_LABEL } from "../format";
import { useApi } from "../hooks/useApi";

function UserForm({ user, sites, onDone }: { user: UserRow | null; sites: Site[]; onDone: () => void }) {
  const run = useAction();
  const [role, setRole] = useState<Role>(user?.role ?? "pharmacist");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = {
      username: String(f.get("username")), role, active: f.get("active") === "on",
      password: f.get("password") ? String(f.get("password")) : null,
      site_ids: f.getAll("site").map(Number),
    };
    if (!user && !body.password) { await run(async () => { throw new Error("Mot de passe requis pour un nouveau compte"); }); return; }
    if (await run(() => (user ? put(`/users/${user.id}`, body) : post("/users", body)), "Compte enregistré")) onDone();
  }
  return (
    <form className="form grid-form" onSubmit={submit}>
      <label>Utilisateur<input name="username" required pattern="[A-Za-z0-9_.@\-]+" defaultValue={user?.username} autoComplete="off" /></label>
      <label>Rôle<select value={role} onChange={(e) => setRole(e.target.value as Role)}>
        <option value="pharmacist">Pharmacien (ses sites, consultation)</option>
        <option value="integrator">Intégrateur (tout, configuration)</option></select></label>
      <label>Mot de passe<input name="password" type="password" minLength={10} autoComplete="new-password" placeholder={user ? "inchangé si vide" : "10 caractères minimum"} /></label>
      <label className="check"><input type="checkbox" name="active" defaultChecked={user ? !!user.active : true} /> Compte actif</label>
      {role === "pharmacist" && <fieldset className="span2"><legend>Sites accessibles</legend>
        {sites.map((s) => <label key={s.id} className="check"><input type="checkbox" name="site" value={s.id} defaultChecked={user?.site_ids.includes(s.id)} /> {s.name}</label>)}</fieldset>}
      <button className="btn primary">Enregistrer</button>
    </form>
  );
}

export default function Users() {
  const { me } = useAuth();
  const users = useApi<UserRow[]>("/users");
  const sites = useApi<Site[]>("/sites");
  const run = useAction();
  const [edit, setEdit] = useState<UserRow | "new" | null>(null);
  if (users.loading && !users.data) return <Loading />;
  if (users.error && !users.data) return <ErrorState message={users.error} onRetry={users.reload} />;
  const siteName = new Map((sites.data ?? []).map((s) => [s.id, s.name]));
  return (
    <>
      <PageHeader title="Utilisateurs et permissions"><button className="btn primary" onClick={() => setEdit("new")}>Nouveau compte</button></PageHeader>
      <Notice>Les droits sont vérifiés par le serveur sur chaque requête : un pharmacien ne voit jamais les équipements, alertes ou journaux d’une autre pharmacie.</Notice>
      <table className="card"><thead><tr><th>Utilisateur</th><th>Rôle</th><th>Périmètre</th><th>Dernière connexion</th><th /></tr></thead>
        <tbody>{(users.data ?? []).map((u) => (
          <tr key={u.id}><td><b>{u.username}</b> {!u.active && <Badge tone="muted">désactivé</Badge>}</td><td>{ROLE_LABEL[u.role]}</td>
            <td>{u.role === "integrator" ? <span className="muted">tout</span> :
              (u.site_ids.map((s) => siteName.get(s)).join(", ") || <Badge tone="warn">aucun site</Badge>)}</td>
            <td>{fmtDateTime(u.last_login_ts)}</td>
            <td className="right"><button className="btn small" onClick={() => setEdit(u)}>Modifier</button>
              {u.id !== me?.id && <button className="btn small danger" onClick={async () => {
                if (confirm(`Supprimer le compte « ${u.username} » ?`) && await run(() => del(`/users/${u.id}`), "Compte supprimé")) users.reload();
              }}>Supprimer</button>}</td></tr>))}</tbody></table>
      {edit && <Modal wide title={edit === "new" ? "Nouveau compte" : `Modifier « ${edit.username} »`} onClose={() => setEdit(null)}>
        <UserForm user={edit === "new" ? null : edit} sites={sites.data ?? []} onDone={() => { setEdit(null); users.reload(); }} />
      </Modal>}
    </>
  );
}
