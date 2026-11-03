import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "./AuthContext";

/** Connexion ; au premier lancement (aucun compte), création de son propre compte. */
export function LoginPage() {
  const { login, setup } = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [first, setFirst] = useState(false);
  useEffect(() => { api<{ needed: boolean }>("/setup").then((r) => setFirst(r.needed)).catch(() => undefined); }, []);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const u = String(f.get("u")).trim(), p = String(f.get("p"));
    if (first && p !== String(f.get("p2"))) { setError("Les deux mots de passe sont différents"); return; }
    setBusy(true);
    try { await (first ? setup(u, p) : login(u, p)); setError(""); }
    catch (err) { setError((err as Error).message); if (first) api<{ needed: boolean }>("/setup").then((r) => setFirst(r.needed)).catch(() => undefined); }
    finally { setBusy(false); }
  }
  return (
    <div className="login">
      <form className="card login-card" onSubmit={submit} key={first ? "setup" : "login"}>
        <h1>CamSup</h1>
        {first && <p><b>Bienvenue.</b> Créez votre compte : il sert à régler CamSup (enregistreur, caméras, e-mail) et à
          créer ensuite les comptes des pharmaciens. Gardez ce mot de passe en lieu sûr.</p>}
        <label>{first ? "Votre nom d’utilisateur" : "Utilisateur"}
          <input name="u" autoComplete="username" required autoFocus minLength={first ? 2 : 1} maxLength={80}
                 pattern={first ? "[A-Za-z0-9_.@\\-]+" : undefined} title={first ? "lettres sans accent, chiffres, . _ - @" : undefined} /></label>
        <label>{first ? "Mot de passe (10 caractères au moins)" : "Mot de passe"}
          <input name="p" type="password" autoComplete={first ? "new-password" : "current-password"} required minLength={first ? 10 : 1} /></label>
        {first && <label>Confirmez le mot de passe<input name="p2" type="password" autoComplete="new-password" required minLength={10} /></label>}
        {error && <p className="error-text" role="alert">{error}</p>}
        <button className="btn primary" disabled={busy}>{busy ? (first ? "Création…" : "Connexion…") : (first ? "Créer mon compte" : "Se connecter")}</button>
        <p className="muted small">Accès tracé : connexions et consultations vidéo sont enregistrées dans le journal.</p>
      </form>
    </div>
  );
}
