import type { Disk, RecorderHealth } from "../api/types";
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
        <span>Compteur d’heures du disque : {d.hours == null ? "—" : `${Number(d.hours).toLocaleString("fr-FR")} h (${(d.hours / 8760).toFixed(1)} ans)`}</span>
      </div>
      {d.counters?.length ? (
        <div className="counters">{d.counters.map((c) => {
          const info = COUNTER[c.id] ?? { label: c.label, hint: "" };
          const t = counterTone(c.raw, c.base, c.id);
          const up = c.base !== null && c.raw > c.base;
          return (
            <div key={c.id} className={`counter c-${t}`} title={info.hint}>
              <div className="counter-value">{Number(c.raw ?? 0).toLocaleString("fr-FR")}</div>
              <div className="counter-label">{info.label}</div>
              <div className="counter-hint">{up ? `+${c.raw - (c.base ?? 0)} depuis le ${day(c.since ?? 0)}`
                : c.since ? `stable depuis le ${day(c.since)}` : info.hint}</div>
            </div>
          );
        })}</div>
      ) : <p className="muted small">Secteurs défectueux : non fournis par cet enregistreur (SMART indisponible).</p>}
      {(d.issues ?? []).length > 0 && <div className="error-text">⚠ {d.issues.join(" · ")}</div>}
    </div>
  );
}

/** Secteurs défectueux (réalloués + en attente + illisibles) ; null si le NVR ne les fournit pas. Les erreurs de câble
 *  (CRC) ne sont pas des secteurs du disque. */
export function badSectors(d: Disk): number | null {
  const c = (d.counters ?? []).filter((x) => x.id !== 199);
  return c.length ? c.reduce((n, x) => n + (Number(x.raw) || 0), 0) : null;
}

/** Verdict en clair pour le bandeau du tableau de bord. */
export function diskVerdict(d: Disk): { tone: "ok" | "warn" | "bad"; title: string; text: string } {
  const sectors = badSectors(d);
  const issues = d.issues ?? [];
  if (d.level === "bad") return { tone: "bad", title: "À remplacer", text: issues.join(" · ") || "le disque est en défaut" };
  if (d.level === "warn") return { tone: "warn", title: "À surveiller", text: issues.join(" · ") };
  if (sectors === null) return { tone: "ok", title: "Bon état", text: "d’après le NVR (détail des secteurs non fourni)" };
  return { tone: "ok", title: "Bon état", text: "aucun secteur défectueux" };
}

/** Bandeau « Santé des disques durs » : un verdict en gros par disque, en tête du tableau de bord. */
export function DiskBanner({ recorders }: { recorders: RecorderHealth[] }) {
  const tiles: { key: string; tone: "ok" | "warn" | "bad" | "info"; title: string; text: string; d?: Disk }[] = [];
  for (const r of recorders) {
    if (!r.disks.length) tiles.push({ key: r.host, tone: r.error ? "bad" : "info", title: r.error ? "Disques illisibles" : "Aucun disque",
      text: r.error ? `NVR ${r.host} : ${r.error}` : `NVR ${r.host} : aucun disque détecté` });
    for (const d of r.disks) tiles.push({ key: `${r.host}-${d.name}`, ...diskVerdict(d), d });
  }
  const worst = tiles.some((t) => t.tone === "bad") ? "bad" : tiles.some((t) => t.tone === "warn") ? "warn" : "ok";
  return (
    <section className={`card disk-banner db-${worst}`} aria-label="Santé des disques durs">
      <div className="row"><h2>Santé des disques durs</h2><span className="grow" />
        <span className="muted small">secteurs défectueux, température et usure lus sur le NVR (SMART)</span></div>
      {!tiles.length ? <p className="muted">Premier relevé dans les 30 secondes qui suivent le démarrage.</p> : (
        <div className="db-tiles">{tiles.map((t) => {
          const sectors = t.d ? badSectors(t.d) : null;
          return (
            <div key={t.key} className={`db-tile db-${t.tone}`}>
              <div className="db-verdict">{t.tone === "ok" ? "✔" : t.tone === "info" ? "?" : "⚠"} {t.title}</div>
              <div className="db-name">{t.d ? `${t.d.model || t.d.name} · ${t.d.name}` : ""}</div>
              <div className="db-text">{t.text}</div>
              {t.d && (
                <div className="db-figures">
                  <span><b>{sectors === null ? "—" : sectors.toLocaleString("fr-FR")}</b> secteur{sectors === 1 ? "" : "s"} défectueux</span>
                  <span><b>{t.d.temp != null ? `${t.d.temp} °C` : "—"}</b> température</span>
                  <span><b>{t.d.hours == null ? "—" : `${(t.d.hours / 8760).toFixed(1)} ans`}</b> de marche (compteur du disque)</span>
                </div>
              )}
            </div>
          );
        })}</div>
      )}
    </section>
  );
}
