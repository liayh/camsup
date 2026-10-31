import { FormEvent, useState } from "react";
import { del, post, put } from "../api/client";
import type { Camera, CameraTest, NvrFound, NvrWatch, Site } from "../api/types";
import { isIntegrator, useAuth } from "../auth/AuthContext";
import { DiskHealth } from "../components/DiskHealth";
import { NvrConfig } from "../components/NvrConfig";
import { useCurrent } from "../components/SiteContext";
import { useAction, useToast } from "../components/Toast";
import { Badge, Empty, ErrorState, Loading, Modal, PageHeader } from "../components/ui";
import { ago, fmtDateTime } from "../format";
import { useApi } from "../hooks/useApi";

function state(c: Camera): [tone: "ok" | "bad" | "warn" | "muted", label: string] {
  if (!c.enabled) return ["muted", "Désactivée"];
  if (c.online == null) return ["warn", "Non vérifiée"];
  if (!c.online) return ["bad", c.error || "Hors ligne"];
  return ["ok", "Vue par le NVR"];
}

function CameraForm({ cam, sites, onDone }: { cam: Camera | null; sites: Site[]; onDone: () => void }) {
  const run = useAction();
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = {
      site_id: Number(f.get("site_id")), name: String(f.get("name")), brand: String(f.get("brand")),
      host: String(f.get("host")), port_http: Number(f.get("port_http") || 80), username: String(f.get("username") || ""),
      password: f.get("password") ? String(f.get("password")) : cam ? null : "", channel: Number(f.get("channel") || 1),
      enabled: f.get("enabled") === "on",
    };
    if (await run(() => (cam ? put(`/cameras/${cam.id}`, body) : post("/cameras", body)), "Caméra enregistrée")) onDone();
  }
  return (
    <form className="form grid-form" onSubmit={submit}>
      <label>Site<select name="site_id" required defaultValue={cam?.site_id ?? ""}>
        <option value="" disabled>Choisir…</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label>Nom<input name="name" required maxLength={120} defaultValue={cam?.name} /></label>
      <label>Marque du NVR<select name="brand" defaultValue={cam?.brand ?? "dahua"}><option value="dahua">Dahua</option><option value="hikvision">Hikvision</option></select></label>
      <label>Adresse du NVR<input name="host" required defaultValue={cam?.host} /></label>
      <label>Port HTTP du NVR<input name="port_http" type="number" defaultValue={cam?.port_http ?? 80} /></label>
      <label>Canal sur le NVR<input name="channel" type="number" min={1} defaultValue={cam?.channel ?? 1} /></label>
      <label>Utilisateur du NVR<input name="username" autoComplete="off" defaultValue={cam?.username} /></label>
      <label>Mot de passe du NVR<input name="password" type="password" autoComplete="new-password" placeholder={cam ? "inchangé si vide" : ""} /></label>
      <label className="check"><input type="checkbox" name="enabled" defaultChecked={cam ? !!cam.enabled : true} /> Surveillée</label>
      <button className="btn primary">Enregistrer</button>
    </form>
  );
}

/** Nouvel enregistreur : identifiants saisis une fois, CamSup liste ses caméras, on coche celles à surveiller. */
/** Identifiants communs à toutes les caméras d'un NVR (vérifiés sur le NVR avant d'être enregistrés). */
function NvrCredentialsForm({ cam, onDone }: { cam: Camera; onDone: () => void }) {
  const run = useAction();
  const [f, setF] = useState({ port_http: cam.port_http, username: cam.username, password: "" });
  async function submit(e: FormEvent) {
    e.preventDefault();
    const r = await run(() => put<{ updated: number }>("/nvr/credentials", { host: cam.host, ...f, password: f.password || null }));
    if (r) onDone();
  }
  return (
    <form className="grid-form form" onSubmit={submit}>
      <label>Port HTTP<input type="number" min={1} max={65535} value={f.port_http} onChange={(e) => setF({ ...f, port_http: Number(e.target.value) })} /></label>
      <label>Utilisateur du NVR<input required autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></label>
      <label>Mot de passe du NVR<input type="password" autoComplete="new-password" placeholder="inchangé si vide" value={f.password}
        onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
      <button className="btn primary">Vérifier et enregistrer</button>
    </form>
  );
}

type Rescan = NvrFound & { host: string; port_http: number; username: string; site_id: number };

function NvrWizard({ sites, current, existing, onDone }: { sites: Site[]; current: number; existing?: Rescan; onDone: (site: number) => void }) {
  const run = useAction();
  const [siteId, setSiteId] = useState<number | "new">(existing?.site_id ?? (sites.some((s) => s.id === current) ? current : sites[0]?.id) ?? "new");
  const [siteName, setSiteName] = useState("");
  const [nvr, setNvr] = useState({ host: existing?.host ?? "", port_http: existing?.port_http ?? 80,
                                   username: existing?.username ?? "admin", password: "" });
  const [found, setFound] = useState<NvrFound | null>(existing ?? null);
  const [names, setNames] = useState<Record<number, string>>(Object.fromEntries((existing?.channels ?? []).map((c) => [c.channel, c.name])));
  const [picked, setPicked] = useState<Set<number>>(new Set((existing?.channels ?? []).filter((c) => !c.known).map((c) => c.channel)));
  const [busy, setBusy] = useState(false);

  async function connect(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const r = await run(() => post<NvrFound>("/nvr/discover", nvr));
    setBusy(false);
    if (!r) return;
    setFound(r);
    setNames(Object.fromEntries(r.channels.map((c) => [c.channel, c.name])));
    setPicked(new Set(r.channels.filter((c) => !c.known).map((c) => c.channel)));
  }
  async function add() {
    if (!found) return;
    let site = siteId;
    if (site === "new") {
      const r = await run(() => post<{ id: number }>("/sites", { name: siteName.trim(), notes: "" }));
      if (!r) return;
      site = r.id;
    }
    const channels = [...picked].sort((a, b) => a - b).map((ch) => ({ channel: ch, name: names[ch] || `Caméra ${ch}` }));
    const r = await run(() => post<{ added: number }>("/nvr", { ...nvr, password: existing ? null : nvr.password, site_id: site, channels }),
                        `${channels.length} caméra(s) ajoutée(s)`);
    if (r) onDone(site);
  }

  const free = found ? found.channels.filter((c) => !c.known).map((c) => c.channel) : [];
  return (
    <>
      {existing ? <p className="muted">Enregistreur {existing.host}, identifiants déjà enregistrés.</p> : <>
      <form className="grid-form form" onSubmit={connect}>
        <label>Pharmacie (site)<select value={siteId} onChange={(e) => setSiteId(e.target.value === "new" ? "new" : Number(e.target.value))}>
          {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}<option value="new">+ Nouvelle pharmacie…</option></select></label>
        {siteId === "new" && <label>Nom de la pharmacie<input required value={siteName} onChange={(e) => setSiteName(e.target.value)} /></label>}
        <label>Adresse IP du NVR<input required value={nvr.host} placeholder="192.168.1.108" onChange={(e) => setNvr({ ...nvr, host: e.target.value })} /></label>
        <label>Port HTTP<input type="number" min={1} max={65535} value={nvr.port_http} onChange={(e) => setNvr({ ...nvr, port_http: Number(e.target.value) })} /></label>
        <label>Utilisateur du NVR<input required autoComplete="off" value={nvr.username} onChange={(e) => setNvr({ ...nvr, username: e.target.value })} /></label>
        <label>Mot de passe du NVR<input type="password" autoComplete="new-password" value={nvr.password} onChange={(e) => setNvr({ ...nvr, password: e.target.value })} /></label>
        <button className="btn primary" disabled={busy}>{busy ? "Connexion…" : "Se connecter au NVR"}</button>
      </form>
      <p className="muted small">NVR Dahua. Le PC doit être sur le même réseau que le NVR. Un compte du NVR en lecture seule suffit.</p>
      </>}
      {found && (
        <>
          <div className="row"><Badge tone="ok">Connecté</Badge> {found.model}{found.serial ? ` · n° ${found.serial}` : ""} ·
            {found.channels.length} caméra(s) branchée(s), {free.length} nouvelle(s)
            <button type="button" className="btn small" onClick={() => setPicked(new Set(free))}>Toutes</button>
            <button type="button" className="btn small" onClick={() => setPicked(new Set())}>Aucune</button></div>
          {!found.channels.length ? <Empty>Aucune caméra branchée sur ce NVR.</Empty> : (
            <table className="scroll-table">
              <thead><tr><th /><th>Canal</th><th>Nom dans CamSup</th><th>État</th></tr></thead>
              <tbody>{found.channels.map((c) => (
                <tr key={c.channel}>
                  <td><input type="checkbox" disabled={c.known} checked={picked.has(c.channel)} aria-label={`Canal ${c.channel}`}
                    onChange={() => setPicked((s) => { const n = new Set(s); if (n.has(c.channel)) n.delete(c.channel); else n.add(c.channel); return n; })} /></td>
                  <td>{c.channel}</td>
                  <td>{c.known ? <span className="muted">déjà ajoutée</span>
                    : <input value={names[c.channel] ?? ""} maxLength={120} onChange={(e) => setNames({ ...names, [c.channel]: e.target.value })} />}</td>
                  <td><Badge tone={c.state === "Connected" ? "ok" : "bad"}>{CONN[c.state] ?? c.state}</Badge></td>
                </tr>))}</tbody>
            </table>
          )}
          <div className="row">
            <button className="btn primary" onClick={add} disabled={!picked.size || (siteId === "new" && !siteName.trim())}>
              Ajouter {picked.size} caméra{picked.size > 1 ? "s" : ""}</button>
          </div>
        </>
      )}
    </>
  );
}

const CONN: Record<string, string> = { Connected: "connectée", NoVideo: "plus d’images", Unconnect: "déconnectée", Empty: "canal vide" };

/** État d'une caméra : ce qu'en dit son NVR (Dahua) s'il est relevé, sinon le test de joignabilité. */
function camState(c: Camera, w: NvrWatch | undefined): [tone: "ok" | "bad" | "warn" | "muted", label: string] {
  const seen = w?.cameras.find((x) => x.id === c.id);
  if (c.enabled && seen) return [seen.state === "Connected" ? "ok" : "bad", CONN[seen.state] ?? seen.state];
  return state(c);
}

/** « NVR et disques » de la pharmacie choisie : chaque enregistreur avec ses disques, puis ses caméras (canaux). */
export default function Cameras() {
  const { me } = useAuth();
  const admin = isIntegrator(me);
  const cur = useCurrent();
  const ov = { data: cur.ov, loading: cur.loading, error: cur.error, reload: cur.reload };
  const sites = useApi<Site[]>("/sites");
  const run = useAction();
  const toast = useToast();
  const [edit, setEdit] = useState<Camera | "new" | null>(null);
  const [wizard, setWizard] = useState<Rescan | true | null>(null);
  const [creds, setCreds] = useState<Camera | null>(null);
  const [rename, setRename] = useState<Site | null>(null);

  if (ov.loading && !ov.data) return <Loading />;
  if (ov.error && !ov.data) return <ErrorState message={ov.error} onRetry={ov.reload} />;
  const cams = cur.cams;
  const here = sites.data?.find((s) => s.id === cur.site);
  const groups = new Map<string, Camera[]>();
  for (const c of cams) groups.set(`${c.brand}|${c.host}`, [...(groups.get(`${c.brand}|${c.host}`) ?? []), c]);

  const test = async (c: Camera) => {
    const r = await run(() => post<CameraTest>(`/cameras/${c.id}/test`));
    if (!r) return;
    if (!r.ok) toast(`${c.name} : échec, ${r.error}`, "bad");
    else if (r.state !== undefined) toast(`${c.name} : NVR joignable, caméra « ${r.state} », dernier enregistrement ${r.last_record ?? "aucun sur 24 h"} (heure du NVR ${r.nvr_time})`, r.state === "Connected" ? "ok" : "bad");
    else toast(`${c.name} : enregistreur joignable, ${r.disks} disque(s)`, "ok");
  };
  const rescan = async (c: Camera) => {
    const r = await run(() => post<Rescan>("/nvr/rescan", { host: c.host }));
    if (r) setWizard(r);
  };
  // la pharmacie (site) se gère ici : son nom, et la suppression d'une pharmacie qui n'a plus de caméra
  const renameSite = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!rename) return;
    const name = String(new FormData(e.currentTarget).get("name")).trim();
    if (await run(() => put(`/sites/${rename.id}`, { name, notes: rename.notes }), "Pharmacie renommée")) { setRename(null); sites.reload(); ov.reload(); }
  };
  const removeSite = async (s: Site) => {
    if (confirm(`Supprimer la pharmacie « ${s.name} » (aucune caméra) ?`) && await run(() => del(`/sites/${s.id}`), "Pharmacie supprimée")) sites.reload();
  };
  const remove = async (c: Camera) => {
    if (confirm(`Supprimer « ${c.name} » ?`) && await run(() => del(`/cameras/${c.id}`), "Caméra supprimée")) ov.reload();
  };

  return (
    <>
      <PageHeader title="NVR et disques" sub={cur.name}>
        {admin && here && <button className="btn small" onClick={() => setRename(here)}>Renommer la pharmacie</button>}
        {admin && here && !cams.length && <button className="btn small danger" onClick={() => removeSite(here)}>Supprimer la pharmacie</button>}
        {admin && <button className="btn primary" onClick={() => setWizard(true)}>Ajouter un enregistreur</button>}
        {admin && <button className="btn small" onClick={() => setEdit("new")}>Ajouter une caméra à la main</button>}
        <button className="btn small" onClick={ov.reload}>Actualiser</button>
      </PageHeader>
      {!cams.length && <Empty>Aucune caméra. {admin ? "Cliquez sur « Ajouter un enregistreur »." : ""}</Empty>}
      {[...groups.values()].map((list) => {
        const { brand, host } = list[0];
        const health = ov.data?.recorders.find((r) => r.host === host);
        const w = ov.data?.nvr.find((n) => n.host === host);
        return (
          <section key={`${brand}|${host}`} className="card">
            <div className="row"><h2>{brand === "hikvision" ? "Hikvision" : "Dahua"} · {host}
              <span className="muted small"> · {list.length} caméra(s){w ? ` · relevé il y a ${ago(w.checked)}` : ""}</span></h2>
              <span className="grow" />
              {admin && brand === "dahua" && <>
                <button className="btn small" onClick={() => rescan(list[0])}>Chercher de nouvelles caméras</button>
                <button className="btn small" onClick={() => setCreds(list[0])}>Identifiants du NVR</button></>}
            </div>
            {w?.error && <div className="error-text">Enregistreur : {w.error}</div>}
            {admin && brand === "dahua" && <details className="small"><summary>Configuration du NVR</summary><NvrConfig host={host} site={cur.name} /></details>}
            {health?.error && !health.disks.length && <div className="error-text">Disques : {health.error}</div>}
            {health?.disks.map((d) => <DiskHealth key={d.name} d={d} />)}
            <table>
              <thead><tr><th>Caméra</th><th>Canal</th><th>État</th><th>Dernier enregistrement</th>{admin && <th />}</tr></thead>
              <tbody>{[...list].sort((a, b) => a.channel - b.channel).map((c) => {
                const [tone, label] = camState(c, w);
                const last = w?.cameras.find((x) => x.id === c.id)?.last_record;
                return (
                  <tr key={c.id}>
                    <td><b>{c.name}</b></td><td>{c.channel}</td>
                    <td><Badge tone={tone}>{label}</Badge></td>
                    <td>{!c.enabled || !w ? <span className="muted">—</span> : last ?? <Badge tone="bad">aucun sur 24 h</Badge>}</td>
                    {admin && <td className="right actions">
                      <button className="btn small" onClick={() => test(c)}>Tester</button>
                      <button className="btn small" onClick={() => setEdit(c)}>Modifier</button>
                      <button className="btn small danger" onClick={() => remove(c)}>Supprimer</button>
                    </td>}
                  </tr>
                );
              })}</tbody>
              {w && <caption className="muted small">Dernier enregistrement à l’heure du NVR (heure du NVR au relevé : {w.nvr_time || "—"})</caption>}
            </table>
          </section>
        );
      })}
      {rename && (
        <Modal title={`Renommer « ${rename.name} »`} onClose={() => setRename(null)}>
          <form className="form" onSubmit={renameSite}>
            <label>Nom de la pharmacie<input name="name" required maxLength={120} defaultValue={rename.name} autoFocus /></label>
            <button className="btn primary">Enregistrer</button>
          </form>
        </Modal>
      )}
      {wizard && (
        <Modal wide title={wizard === true ? "Ajouter un enregistreur (NVR)" : `Nouvelles caméras du NVR ${wizard.host}`} onClose={() => setWizard(null)}>
          <NvrWizard sites={sites.data ?? []} current={cur.site} existing={wizard === true ? undefined : wizard}
                     onDone={(site) => { setWizard(null); ov.reload(); sites.reload(); cur.setSite(site); }} />
        </Modal>
      )}
      {creds && (
        <Modal title={`Identifiants du NVR ${creds.host}`} onClose={() => setCreds(null)}>
          <p className="muted">Utilisés pour toutes les caméras de cet enregistreur. CamSup vérifie qu’ils fonctionnent avant de les enregistrer.</p>
          <NvrCredentialsForm cam={creds} onDone={() => { setCreds(null); ov.reload(); }} />
        </Modal>
      )}
      {edit && (
        <Modal wide title={edit === "new" ? "Ajouter une caméra (canal du NVR)" : `Modifier « ${edit.name} »`} onClose={() => setEdit(null)}>
          <CameraForm cam={edit === "new" ? null : edit} sites={sites.data ?? []} onDone={() => { setEdit(null); ov.reload(); }} />
        </Modal>
      )}
      <p className="muted small">Disque « à surveiller » : affiché ici seulement. « À remplacer » (secteurs en hausse, seuil SMART
        dépassé, disque en erreur) : fenêtre d’alerte sur le poste et e-mail à l’installateur. Mis à jour {fmtDateTime(ov.data?.now)}.</p>
    </>
  );
}
