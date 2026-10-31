import { ReactNode, useEffect } from "react";

export function PageHeader({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-header">
      <div><h1>{title}</h1>{sub && <div className="subtitle">{sub}</div>}</div>
      <div className="page-actions">{children}</div>
    </div>
  );
}

export function Loading({ label = "Chargement…" }: { label?: string }) {
  return <div className="loading" role="status"><span className="spinner" />{label}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      <strong>Erreur :</strong> {message}
      {onRetry && <button className="btn small" onClick={onRetry}>Réessayer</button>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** Indicateur : `value === null` = inconnu, affiché comme indisponible (jamais comme normal). */
export function Stat({ label, value, tone, hint, unavailable }: {
  label: string; value: ReactNode | null; tone?: "ok" | "warn" | "bad"; hint?: string; unavailable?: string;
}) {
  return (
    <div className={`stat ${value === null ? "stat-unknown" : tone ? `stat-${tone}` : ""}`} title={unavailable || hint}>
      <div className="stat-value">{value === null ? "Indisponible" : value}</div>
      <div className="stat-label">{label}</div>
      {value === null && unavailable && <div className="stat-hint">{unavailable}</div>}
      {value !== null && hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Badge({ tone, children }: { tone: "ok" | "warn" | "bad" | "info" | "muted"; children: ReactNode }) {
  return <span className={`badge b-${tone}`}>{children}</span>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-label={title}>
        <div className="modal-head"><h2>{title}</h2><button className="btn" onClick={onClose} aria-label="Fermer">✕</button></div>
        {children}
      </div>
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn"; children: ReactNode }) {
  return <div className={`notice n-${tone}`}>{children}</div>;
}
