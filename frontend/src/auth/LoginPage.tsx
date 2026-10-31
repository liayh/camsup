import { FormEvent, useState } from "react";
import { useAuth } from "./AuthContext";

export function LoginPage() {
  const { login } = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    try { await login(String(f.get("u")), String(f.get("p"))); setError(""); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <div className="login">
      <form className="card login-card" onSubmit={submit}>
        <h1>CamSup</h1>
        <label>Utilisateur<input name="u" autoComplete="username" required autoFocus /></label>
        <label>Mot de passe<input name="p" type="password" autoComplete="current-password" required /></label>
        {error && <p className="error-text" role="alert">{error}</p>}
        <button className="btn primary" disabled={busy}>{busy ? "Connexion…" : "Se connecter"}</button>
        <p className="muted small">Accès tracé : connexions et consultations vidéo sont enregistrées dans le journal.</p>
      </form>
    </div>
  );
}
