import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL } from "../format";
import { SITE_LABEL, SiteProvider, siteState, useSite } from "./SiteContext";

export const SECTIONS = [
  { path: "/", label: "Tableau de bord" },
  { path: "/video", label: "Vidéo" },
  { path: "/cameras", label: "NVR et disques" },
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
      {!ov.sites.length && <p className="muted small">Aucune pharmacie : ajoutez un enregistreur (NVR et disques).</p>}
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
