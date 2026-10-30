import type { Disk } from "../api/types";
import { fmtBytes } from "../format";
import { Badge } from "./ui";

const LEVEL: Record<string, ["ok" | "warn" | "bad", string]> = { ok: ["ok", "Bon état"], warn: ["warn", "À surveiller"], bad: ["bad", "À remplacer"] };
const COUNTER: Record<number, { label: string; hint: string }> = {
  5: { label: "Secteurs réalloués", hint: "secteurs abîmés déjà remplacés par le disque" },
  197: { label: "Secteurs en attente", hint: "secteurs douteux, pas encore remplacés" },
  198: { label: "Secteurs illisibles", hint: "secteurs que le disque n’a pas pu relire" },
  199: { label: "Erreurs de câble (CRC)", hint: "liaison SATA : câble ou connecteur, pas le disque" },
};
const day = (ts: number) => new Date(ts * 1000).toLocaleDateString("fr-FR");

/** Teinte d'un compteur : 0 = bon ; en hausse depuis le premier relevé = à remplacer ; stable mais non nul = à surveiller. */
export function counterTone(raw: number, base: number | null, id: number): "ok" | "warn" | "bad" {
  if (raw === 0) return "ok";
  if (id !== 199 && base !== null && raw > base) return "bad";
  return id !== 199 && raw >= 50 ? "bad" : "warn";
}

/** Santé d'un disque de NVR : état, température, usure et, surtout, les secteurs défectueux. */
export function DiskHealth({ d }: { d: Disk }) {
  const [tone, label] = LEVEL[d.level] ?? LEVEL.bad;
  const pct = d.total ? Math.round(((d.used ?? 0) * 100) / d.total) : null;
  return (
    <div className={`disk disk-${tone}`}>
      <div className="row"><b>{d.model || d.name}</b><Badge tone={tone}>{label}</Badge>
        <span className="muted small">{d.name}{d.serial ? ` · n° ${d.serial}` : ""} · {d.state}</span></div>
      <div className="row muted small">
        <span>Capacité {fmtBytes(d.total)}{pct !== null ? ` · ${pct} % rempli${pct >= 99 ? " (normal : le NVR écrase les plus anciens)" : ""}` : ""}</span>
        <span>Température {d.temp != null ? `${d.temp} °C` : "—"}</span>
        <span>En marche depuis {d.hours == null ? "—" : `${d.hours.toLocaleString("fr-FR")} h (${(d.hours / 8760).toFixed(1)} ans)`}</span>
      </div>
      {d.counters?.length ? (
        <div className="counters">{d.counters.map((c) => {
          const info = COUNTER[c.id] ?? { label: c.label, hint: "" };
          const t = counterTone(c.raw, c.base, c.id);
          const up = c.base !== null && c.raw > c.base;
          return (
            <div key={c.id} className={`counter c-${t}`} title={info.hint}>
              <div className="counter-value">{c.raw.toLocaleString("fr-FR")}</div>
              <div className="counter-label">{info.label}</div>
              <div className="counter-hint">{up ? `+${c.raw - (c.base ?? 0)} depuis le ${day(c.since ?? 0)}`
                : c.since ? `stable depuis le ${day(c.since)}` : info.hint}</div>
            </div>
          );
        })}</div>
      ) : <p className="muted small">Secteurs défectueux : non fournis par cet enregistreur (SMART indisponible).</p>}
      {d.issues.length > 0 && <div className="error-text">⚠ {d.issues.join(" · ")}</div>}
    </div>
  );
}
