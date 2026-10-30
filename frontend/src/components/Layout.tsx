import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { isIntegrator, useAuth } from "../auth/AuthContext";
import { ROLE_LABEL } from "../format";

export const SECTIONS = [
  { path: "/", label: "État et alertes", icon: "▦" },
  { path: "/video", label: "Voir la vidéo", icon: "▶" },
  { path: "/cameras", label: "Enregistreurs et caméras", icon: "▣" },
  { path: "/sites", label: "Sites", icon: "⌂" },
  { path: "/utilisateurs", label: "Utilisateurs", icon: "☺", integrator: true },
  { path: "/parametres", label: "Paramètres", icon: "⚙" },
] as const;

export function Layout() {
  const { me, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const items = SECTIONS.filter((s) => !("integrator" in s) || isIntegrator(me));
  return (
    <div className={`shell ${open ? "nav-open" : ""}`}>
      <aside className="sidebar">
        <div className="brand">BLL <b>Vision Monitor</b></div>
        <nav aria-label="Navigation principale">
          {items.map((s) => (
            <NavLink key={s.path} to={s.path} end={s.path === "/"} onClick={() => setOpen(false)}
                     className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
              <span className="nav-icon" aria-hidden>{s.icon}</span>{s.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="me">{me?.username}<span className="muted small"> · {ROLE_LABEL[me?.role ?? ""]}</span></div>
          <button className="btn small" onClick={logout}>Déconnexion</button>
        </div>
      </aside>
      <button className="nav-toggle btn" onClick={() => setOpen(!open)} aria-label="Menu">☰</button>
      <main className="content"><Outlet /></main>
    </div>
  );
}
