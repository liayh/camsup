import { FormEvent, useEffect, useState } from "react";
import { post, put } from "../api/client";
import type { EmailSecurity, EmailSettings } from "../api/types";
import { isIntegrator, useAuth } from "../auth/AuthContext";
import { useAction, useToast } from "../components/Toast";
import { Badge, ErrorState, Loading, Notice, PageHeader } from "../components/ui";
import { fmtDateTime } from "../format";
import { useApi } from "../hooks/useApi";

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

/** Serveurs d'envoi courants (réglages publiés par chaque fournisseur). */
export const PROVIDERS: Record<string, { label: string; host: string; port: number; security: EmailSecurity; note?: string }> = {
  gmail: { label: "Gmail", host: "smtp.gmail.com", port: 587, security: "starttls", note: "mot de passe d’application Google obligatoire (compte avec validation en 2 étapes)" },
  outlook: { label: "Outlook / Microsoft 365", host: "smtp.office365.com", port: 587, security: "starttls", note: "l’envoi SMTP doit être autorisé sur le compte" },
  orange: { label: "Orange", host: "smtp.orange.fr", port: 465, security: "ssl" },
  free: { label: "Free", host: "smtp.free.fr", port: 465, security: "ssl" },
  ovh: { label: "OVH", host: "ssl0.ovh.net", port: 465, security: "ssl" },
};
const SECURITY: Record<EmailSecurity, string> = { starttls: "STARTTLS (port 587)", ssl: "SSL/TLS (port 465)", aucune: "Aucune (relais local)" };

function Email() {
  const cfg = useApi<EmailSettings>("/settings/email");
  const run = useAction();
  const [form, setForm] = useState<{ host: string; port: number; security: EmailSecurity; user: string; password: string; sender: string; to: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const toast = useToast();
  useEffect(() => {
    if (cfg.data && !form) {
      const d = cfg.data;
      setForm({ host: d.host, port: d.port, security: d.security, user: d.user, password: "", sender: d.sender, to: d.to.join(", ") });
    }
  }, [cfg.data, form]);
  if (cfg.loading && !cfg.data) return <Loading />;
  if (cfg.error && !cfg.data) return <ErrorState message={cfg.error} onRetry={cfg.reload} />;
  if (!form || !cfg.data) return null;
  const d = cfg.data;
  const set = (k: keyof typeof form, v: string | number) => setForm({ ...form, [k]: v });
  const preset = Object.entries(PROVIDERS).find(([, p]) => p.host === form.host)?.[0] ?? "";
  const choose = (key: string) => {
    const p = PROVIDERS[key];
    if (p) setForm({ ...form, host: p.host, port: p.port, security: p.security });
  };
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    const body = { ...form, password: form.password ? form.password : null };
    if (await run(() => put("/settings/email", body), "Réglages e-mail enregistrés")) { setForm({ ...form, password: "" }); cfg.reload(); }
  }
  const test = async () => {
    setTesting(true);
    try {
      await post("/notify/test");
      toast(`E-mail de test envoyé à ${d.to.join(", ")} : vérifiez la boîte de réception (et les indésirables)`, "ok");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setTesting(false);
      cfg.reload();
    }
  };
  const note = PROVIDERS[preset]?.note;

  return (
    <section className="card"><h2>E-mail à l’installateur</h2>
      <p>{d.configured ? <Badge tone="ok">configuré</Badge> : <Badge tone="bad">non configuré</Badge>}
        {d.source === "fichier .env" && d.configured && <span className="muted small"> (réglé dans le fichier .env ; enregistrer ici le remplace)</span>}</p>
      <p className="muted">Envoyé quand l’enregistreur est injoignable, n’enregistre plus une caméra, ou qu’un disque est à
        remplacer, puis quand c’est rétabli. Le pharmacien, lui, est prévenu par la fenêtre d’alerte du poste.</p>
      <form className="form" onSubmit={submit}>
        <div className="grid-form form">
          <label>Destinataire(s)<input required value={form.to} onChange={(e) => set("to", e.target.value)}
            placeholder="vous@exemple.fr, collegue@exemple.fr" /></label>
          <label>Fournisseur<select value={preset} onChange={(e) => choose(e.target.value)}>
            <option value="">Autre (saisie manuelle)</option>
            {Object.entries(PROVIDERS).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}</select></label>
          <label>Serveur d’envoi (SMTP)<input required value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="smtp.exemple.fr" /></label>
          <label>Port<input type="number" min={1} max={65535} required value={form.port} onChange={(e) => set("port", Number(e.target.value))} /></label>
          <label>Sécurité<select value={form.security} onChange={(e) => set("security", e.target.value)}>
            {(Object.keys(SECURITY) as EmailSecurity[]).map((k) => <option key={k} value={k}>{SECURITY[k]}</option>)}</select></label>
          <label>Compte d’envoi (identifiant)<input value={form.user} onChange={(e) => set("user", e.target.value)} autoComplete="off" placeholder="adresse du compte" /></label>
          <label>Mot de passe du compte<input type="password" value={form.password} onChange={(e) => set("password", e.target.value)}
            autoComplete="new-password" placeholder={d.has_password ? "inchangé si vide" : ""} /></label>
          <label>Adresse d’expéditeur (facultatif)<input value={form.sender} onChange={(e) => set("sender", e.target.value)} placeholder="par défaut : le compte d’envoi" /></label>
        </div>
        {note && <Notice tone="warn">{PROVIDERS[preset].label} : {note}.</Notice>}
        <div className="row">
          <button className="btn primary">Enregistrer</button>
          <button type="button" className="btn" onClick={test} disabled={!d.configured || testing}>
            {testing ? "Envoi…" : "Envoyer un e-mail de test"}</button>
          {d.last && (d.last.ok ? <span className="muted small">Dernier envoi réussi : {fmtDateTime(d.last.ts)}</span>
            : <span className="error-text small">Dernier envoi en échec ({fmtDateTime(d.last.ts)}) : {d.last.error}</span>)}
        </div>
        <p className="muted small">Enregistrez avant de tester. Le mot de passe est chiffré sur ce poste et n’est jamais réaffiché.</p>
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
      {isIntegrator(me) && <Email />}
    </>
  );
}
