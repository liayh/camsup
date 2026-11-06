import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL } from "../format";
import { SITE_LABEL, SiteProvider, siteState, useSite } from "./SiteContext";

export const SECTIONS = [
  { path: "/", label: "Tableau de bord" },
  { path: "/video", label: "Back-up" },
  { path: "/alertes", label: "Alertes" },
] as const;

const Logo = () => (
  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M3 8a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="m16 11 5-3v8l-5-3" />
  </svg>
);

/** Colonne « Pharmacies » : chaque site avec son nombre de caméras et son état ; un clic l'affiche dans toutes les pages. */
function Sites({ filter }: { filter: string }) {
  const { ov, site, setSite } = useSite();
  if (!ov) return null;
  const f = filter.trim().toLowerCase();
  const list = ov.sites.filter((s) => !f || s.name.toLowerCase().includes(f)
    || ov.cameras.some((c) => c.site_id === s.id && c.name.toLowerCase().includes(f)));
  return (
    <aside className="sites" aria-label="Pharmacies">
      <div className="sites-head"><h2>Pharmacies</h2><span className="mono small muted">{ov.sites.length} site{ov.sites.length > 1 ? "s" : ""}</span></div>
      {!ov.sites.length && <p className="muted small">Aucune pharmacie : ajoutez un enregistreur (Tableau de bord).</p>}
      {ov.sites.length > 0 && !list.length && <p className="muted small">Aucune pharmacie ni caméra ne correspond.</p>}
      {list.map((s) => {
        const tone = siteState(ov, s.id);
        const n = ov.cameras.filter((c) => c.site_id === s.id).length;
        return (
          <button key={s.id} className={`site-btn ${s.id === site ? "on" : ""}`} aria-pressed={s.id === site} onClick={() => setSite(s.id)}>
            <span className="site-txt"><span className="site-name">{s.name}</span><span className="mono small muted">{n} caméra{n > 1 ? "s" : ""}</span></span>
            <span className={`pill pill-${tone}`}>{SITE_LABEL[tone]}</span>
          </button>
        );
      })}
    </aside>
  );
}

/** Bandeau de licence : bientôt expirée (orange) ou expirée / non activée (rouge, surveillance et vidéo arrêtées). */
function LicenceBanner() {
  const { ov } = useSite();
  const l = ov?.licence;
  if (!l || l.etat === "active" || l.etat === "sans") return null;
  const fin = l.fin ? new Date(l.fin).toLocaleDateString("fr-FR") : "";
  return (
    <div className={`licence-banner ${l.actif ? "warn" : "bad"}`} role="alert">
      {l.actif
        ? <>Licence CamSup : se termine le <b>{fin}</b> (dans {l.jours} jour{(l.jours ?? 0) > 1 ? "s" : ""}). Demandez le nouveau code à votre installateur.</>
        : <><b>Licence CamSup {l.etat === "absente" ? "non activée" : `expirée depuis le ${fin}`}</b> : surveillance, direct et vidéo arrêtés.</>}
      {" "}<Link to="/parametres">Entrer le code</Link>
    </div>
  );
}

/** Bandeau de l'installateur : les alertes ne partent pas par e-mail (compte d'envoi absent ou envoi en échec). */
function EmailBanner() {
  const { ov } = useSite();
  if (!ov || ov.me.role !== "integrator") return null;
  const failed = ov.email_status && !ov.email_status.ok;
  const unsent = ov.email_unsent ?? 0;
  if (ov.email && !failed && !unsent) return null;
  return (
    <div className="licence-banner bad" role="alert">
      <b>Les alertes ne partent pas par e-mail</b>
      {!ov.email ? " : compte d’envoi non configuré."
        : failed ? ` : dernier envoi en échec (${ov.email_status!.error}).`
        : " : envoi en cours ou en échec."}
      {unsent > 0 && ` ${unsent} alerte${unsent > 1 ? "s" : ""} en attente (nouvel essai toutes les 5 min).`}
      {" "}<Link to="/parametres">Régler l’e-mail</Link>
    </div>
  );
}

function Frame() {
  const { me, logout } = useAuth();
  const [filter, setFilter] = useState("");
  return (
    <div className="app">
      <header className="topbar">
        <div className="logo"><Logo /><span>CamSup</span></div>
        <nav aria-label="Navigation principale">
          {SECTIONS.map((s) => (
            <NavLink key={s.path} to={s.path} end={s.path === "/"} className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>{s.label}</NavLink>
          ))}
        </nav>
        <span className="grow" />
        <label className="sr-only" htmlFor="search">Rechercher une pharmacie ou une caméra</label>
        <input id="search" className="search" type="search" placeholder="Pharmacie ou caméra…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <NavLink to="/parametres" className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>Paramètres</NavLink>
        <span className="small muted">{me?.username} · {ROLE_LABEL[me?.role ?? ""]}</span>
        <button className="btn small" onClick={logout}>Déconnexion</button>
      </header>
      <LicenceBanner />
      <EmailBanner />
      <div className="body">
        <Sites filter={filter} />
        <main className="content"><Outlet /></main>
      </div>
    </div>
  );
}

export function Layout() {
  return <SiteProvider><Frame /></SiteProvider>;
}
