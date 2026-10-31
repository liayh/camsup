import { FormEvent, useEffect, useState } from "react";
import { post, put } from "../api/client";
import type { EmailSecurity, EmailSettings } from "../api/types";
import { isIntegrator, useAuth } from "../auth/AuthContext";
import { useAction, useToast } from "../components/Toast";
import { Badge, ErrorState, Loading, Notice, PageHeader } from "../components/ui";
import { fmtDateTime } from "../format";
import { useApi } from "../hooks/useApi";
import Users from "./Users";

function Account() {
  const run = useAction();
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    if (await run(() => post("/me/password", { current: f.get("cur"), new: f.get("new") }), "Mot de passe changé")) form.reset();
  }
  return (
    <section className="card"><h2>Mon compte</h2>
      <form className="form grid-form" onSubmit={submit}>
        <label>Mot de passe actuel<input name="cur" type="password" required autoComplete="current-password" /></label>
        <label>Nouveau (10 caractères minimum)<input name="new" type="password" required minLength={10} autoComplete="new-password" /></label>
        <button className="btn primary">Changer le mot de passe</button>
      </form>
    </section>
  );
}

const SECURITY: Record<EmailSecurity, string> = { starttls: "STARTTLS (port 587)", ssl: "SSL/TLS (port 465)", aucune: "Aucune (relais local)" };
type Form = { host: string; port: number; security: EmailSecurity; user: string; password: string; sender: string; to: string };

/** E-mail à l'installateur : adresse et mot de passe du compte d'envoi suffisent, le serveur est trouvé par CamSup. */
function Email() {
  const cfg = useApi<EmailSettings>("/settings/email");
  const run = useAction();
  const toast = useToast();
  const [form, setForm] = useState<Form | null>(null);
  const [auto, setAuto] = useState(true);            // serveur d'envoi déduit de l'adresse
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (cfg.data && !form) {
      const d = cfg.data;
      setForm({ host: d.host, port: d.port, security: d.security, user: d.user, password: "", sender: d.sender, to: d.to.join(", ") });
      setAuto(!d.host);                                // serveur déjà réglé : gardé tel quel
    }
  }, [cfg.data, form]);
  if (cfg.loading && !cfg.data) return <Loading />;
  if (cfg.error && !cfg.data) return <ErrorState message={cfg.error} onRetry={cfg.reload} />;
  if (!form || !cfg.data) return null;
  const d = cfg.data;
  const set = (k: keyof Form, v: string | number) => setForm({ ...form, [k]: v });

  /** Enregistre puis envoie aussitôt un e-mail de test : le résultat dit tout de suite si ça marche. */
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    try {
      const body = { ...form, host: auto ? "" : form.host, password: form.password ? form.password : null };
      const saved = await run(() => put<EmailSettings>("/settings/email", body));
      if (!saved) return;
      setForm({ ...form, host: saved.host, port: saved.port, security: saved.security, password: "" });
      try {
        await post("/notify/test");
        toast(`Réglages enregistrés. E-mail de test envoyé à ${saved.to.join(", ")} : vérifiez la boîte de réception (et les indésirables)`, "ok");
      } catch (err) {
        toast(`Réglages enregistrés, mais l’envoi a échoué : ${(err as Error).message}`, "bad");
      }
    } finally {
      setBusy(false);
      cfg.reload();
    }
  }

  return (
    <section className="card"><h2>E-mail à l’installateur</h2>
      <p>{d.configured ? <Badge tone="ok">configuré</Badge> : <Badge tone="bad">non configuré</Badge>}
        {d.configured && <span className="muted small"> envoi par {d.host} ({d.user || "sans compte"})</span>}</p>
      <p className="muted">Envoyé quand l’enregistreur est injoignable, n’enregistre plus une caméra, ou qu’un disque est à
        remplacer, puis quand c’est rétabli. Le pharmacien, lui, est prévenu par la fenêtre d’alerte du poste.</p>
      <form className="form" onSubmit={submit}>
        <div className="grid-form form">
          <label>Adresse e-mail qui envoie les alertes<input type="email" required value={form.user} onChange={(e) => set("user", e.target.value)}
            autoComplete="off" placeholder="alertes@exemple.fr" /></label>
          <label>Mot de passe de cette adresse<input type="password" value={form.password} onChange={(e) => set("password", e.target.value)}
            autoComplete="new-password" placeholder={d.has_password ? "inchangé si vide" : ""} required={!d.has_password} /></label>
          <label>Qui reçoit les alertes<input required value={form.to} onChange={(e) => set("to", e.target.value)}
            placeholder="vous@exemple.fr, collegue@exemple.fr" /></label>
        </div>
        {d.app_password && <Notice tone="warn">Adresse {d.app_password} : le mot de passe habituel est refusé, il faut un
          « mot de passe d’application » (réglages de sécurité du compte, validation en 2 étapes activée).</Notice>}
        <details>
          <summary className="muted small">Réglages avancés (serveur d’envoi : trouvé automatiquement d’après l’adresse)</summary>
          <label className="check"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Trouver le serveur automatiquement</label>
          {!auto && (
            <div className="grid-form form">
              <label>Serveur d’envoi (SMTP)<input required value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="smtp.exemple.fr" /></label>
              <label>Port<input type="number" min={1} max={65535} required value={form.port} onChange={(e) => set("port", Number(e.target.value))} /></label>
              <label>Sécurité<select value={form.security} onChange={(e) => set("security", e.target.value)}>
                {(Object.keys(SECURITY) as EmailSecurity[]).map((k) => <option key={k} value={k}>{SECURITY[k]}</option>)}</select></label>
              <label>Adresse d’expéditeur (facultatif)<input value={form.sender} onChange={(e) => set("sender", e.target.value)} placeholder="par défaut : l’adresse qui envoie" /></label>
            </div>
          )}
        </details>
        <div className="row">
          <button className="btn primary" disabled={busy}>{busy ? <><span className="spinner" /> Recherche du serveur et envoi…</> : "Enregistrer et envoyer un e-mail de test"}</button>
          {d.last && (d.last.ok ? <span className="muted small">Dernier envoi réussi : {fmtDateTime(d.last.ts)}</span>
            : <span className="error-text small">Dernier envoi en échec ({fmtDateTime(d.last.ts)}) : {d.last.error}</span>)}
        </div>
        <p className="muted small">Le mot de passe est chiffré sur ce poste et n’est jamais réaffiché. Pour ne rien saisir
          dans les pharmacies, ces réglages peuvent être livrés avec l’installateur (fichier email.env).</p>
      </form>
    </section>
  );
}

export default function Settings() {
  const { me } = useAuth();
  return (
    <>
      <PageHeader title="Paramètres" />
      <Account />
      {isIntegrator(me) && <><Users /><Email /></>}
    </>
  );
}
