import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, post } from "../api/client";
import type { Camera, ExportJob, Overview, Preview } from "../api/types";
import { useAction, useToast } from "../components/Toast";
import { Badge, Empty, ErrorState, Loading, Notice, PageHeader } from "../components/ui";
import { fmtBytes, fmtDateTime } from "../format";
import { useApi } from "../hooks/useApi";

const pad = (n: number) => String(n).padStart(2, "0");
const day = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hms = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
const SPEEDS = [1, 2, 4, 8];

/** Période saisie (heure du PC) → horodatages ; une fin avant le début = le lendemain (période sur minuit). */
export function period(date: string, from: string, to: string): [number, number] | null {
  const a = new Date(`${date}T${from}`).getTime() / 1000;
  let b = new Date(`${date}T${to}`).getTime() / 1000;
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  if (b <= a) b += 86400;
  return [a, b];
}

/** Nom proposé pour le fichier enregistré (même forme que celui donné par le serveur). */
export function fileName(site: string, start: number): string {
  const d = new Date(start * 1000);
  return `Video_${site}_${day(d)}_${pad(d.getHours())}h${pad(d.getMinutes())}`.replace(/[^\w-]/g, "_") + ".zip";
}

type SaveHandle = { name: string; createWritable: () => Promise<WritableStream> };
type SavePicker = (o: { suggestedName: string; types: { description: string; accept: Record<string, string[]> }[] }) => Promise<SaveHandle>;
type Saving = { state: "choix" | "attente" | "en_cours" | "envoi" | "fini" | "erreur"; text: string; id?: number };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const CHUNK = 300;              // la vidéo arrive par morceaux de 5 min (`preview.MINUTES`), lus à la suite
const MAX_VIEW = 4;             // caméras lues ensemble
const MAX_HOURS = 6;            // tranche horaire affichable d'un coup
/** Le navigateur annonce-t-il savoir lire le H.265 ? Oui → copie immédiate ; non → conversion par CamSup. */
const HEVC = typeof document !== "undefined" && document.createElement("video").canPlayType('video/mp4; codecs="hvc1.1.6.L150.90"') !== "";

export type Marks = { start?: number; end?: number };
type Range = { from: number; to: number };

/** Début de la tranche ramené à la minute (les morceaux du serveur commencent à la minute). */
export const rangeStart = (ts: number) => ts - (((ts % 60) + 60) % 60);
/** Morceau de 5 min contenant l'instant `ts` (ramené dans la tranche), et position dans ce morceau. */
export function locate(ts: number, r: Range): [chunk: number, offset: number] {
  ts = Math.min(Math.max(ts, r.from), r.to);
  const base = rangeStart(r.from);
  const k = Math.max(0, Math.min(Math.floor((ts - base) / CHUNK), Math.ceil((r.to - base) / CHUNK) - 1));
  return [k, ts - (base + k * CHUNK)];
}
/** Position (0–100 %) d'un instant sur la barre de la tranche ; null s'il est en dehors. */
export function markPos(ts: number | undefined, r: Range): number | null {
  if (ts === undefined || ts < r.from || ts > r.to) return null;
  return ((ts - r.from) / (r.to - r.from)) * 100;
}

/** Lecture d'une tranche horaire, 1 à 4 caméras synchronisées, avec ciseaux début / fin sur la barre de temps. */
function Viewer({ cams, siteId, siteName, maxMinutes }: { cams: Camera[]; siteId: number; siteName: string; maxMinutes: number }) {
  const run = useAction();
  const toast = useToast();
  const now = new Date();
  const [sel, setSel] = useState<number[]>(cams[0] ? [cams[0].id] : []);
  const [date, setDate] = useState(day(now));
  const [from, setFrom] = useState(hms(new Date(now.getTime() - 3600000)).slice(0, 5));
  const [to, setTo] = useState(hms(now).slice(0, 5));
  const [range, setRange] = useState<Range | null>(null);
  const [shown, setShown] = useState<number[]>([]);
  const [chunk, setChunk] = useState(0);
  const [jobs, setJobs] = useState<Record<string, Preview>>({});
  const [mode, setMode] = useState<"copie" | "conversion">(HEVC ? "copie" : "conversion");
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [marks, setMarks] = useState<Marks>({});
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState<Saving | null>(null);
  const vids = useRef<Record<number, HTMLVideoElement | null>>({});
  const asked = useRef<Set<string>>(new Set());
  const startAt = useRef(0);
  const name = useMemo(() => new Map(cams.map((c) => [c.id, c.name])), [cams]);
  const base = range ? rangeStart(range.from) : 0;
  const chunks = range ? Math.ceil((range.to - base) / CHUNK) : 0;

  // morceau en cours et le suivant (lecture sans attente), pour chaque caméra affichée
  useEffect(() => {
    if (!range) return;
    for (const k of [chunk, chunk + 1].filter((k) => k < chunks)) {
      for (const cam of shown) {
        const key = `${mode}-${k}-${cam}`;
        if (asked.current.has(key)) continue;
        asked.current.add(key);
        run(() => post<Preview>("/previews", { camera_id: cam, start_ts: base + k * CHUNK, mode }))
          .then((r) => { if (r) setJobs((j) => ({ ...j, [key]: r })); else asked.current.delete(key); });
      }
    }
  }, [range, chunk, shown, mode, chunks, base, run]);

  // suit la préparation (téléchargement depuis le NVR, puis copie ou conversion)
  useEffect(() => {
    const pending = Object.entries(jobs).filter(([, j]) => j.state === "attente" || j.state === "en_cours");
    if (!pending.length) return;
    const t = setTimeout(async () => {
      for (const [key, j] of pending) {
        try {
          const r = await api<Preview>(`/previews/${j.id}`);
          setJobs((all) => ({ ...all, [key]: r }));
        } catch { /* nouvel essai au tour suivant */ }
      }
    }, 1000);
    return () => clearTimeout(t);
  }, [jobs]);

  const job = (cam: number) => jobs[`${mode}-${chunk}-${cam}`];
  const ready = shown.filter((c) => job(c)?.state === "pret");
  const master = ready[0];
  const all = () => ready.map((c) => vids.current[c]).filter((v): v is HTMLVideoElement => !!v);
  const at = base + chunk * CHUNK + Math.floor(pos);

  const show = (e: FormEvent) => {
    e.preventDefault();
    const p = period(date, from, to);
    if (!p || !sel.length) return;
    const r = { from: p[0], to: Math.min(p[1], Math.floor(Date.now() / 1000)) };
    if (r.to <= r.from) { toast("Cette tranche n’est pas encore enregistrée", "bad"); return; }
    if (r.to - r.from > MAX_HOURS * 3600) { toast(`${MAX_HOURS} h au plus d’un coup`, "bad"); return; }
    const [k, off] = locate(r.from, r);
    startAt.current = off;
    setRange(r); setShown(sel); setChunk(k); setPos(off); setPlaying(true); setMarks({}); setSaving(null);
  };
  const goTo = (ts: number) => {
    if (!range) return;
    const [k, off] = locate(ts, range);
    setPos(off);
    if (k === chunk) { for (const v of all()) v.currentTime = off; return; }
    startAt.current = off;
    setChunk(k);
  };
  const sync = (t: number) => {
    setPos(t);
    if (range && base + chunk * CHUNK + t >= range.to) { for (const v of all()) v.pause(); setPlaying(false); return; }
    for (const v of all()) if (Math.abs(v.currentTime - t) > 0.5) v.currentTime = t;
  };
  const next = () => {
    if (chunk + 1 >= chunks) { setPlaying(false); return; }
    startAt.current = 0; setPos(0); setChunk(chunk + 1);
  };
  const toggle = () => {
    const go = !playing;
    setPlaying(go);
    for (const v of all()) if (go) v.play().catch(() => undefined); else v.pause();
  };
  useEffect(() => { for (const v of all()) v.playbackRate = speed; });
  const failed = () => {
    if (mode !== "copie") return;
    toast("Ce navigateur ne lit pas la vidéo d’origine (H.265) : conversion par CamSup, un peu plus longue", "info");
    startAt.current = pos;
    setMode("conversion");
  };
  const pick = (id: number) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length < MAX_VIEW ? [...s, id] : s));
  const dur = marks.start !== undefined && marks.end !== undefined ? marks.end - marks.start : null;
  const working = !!saving && !["fini", "erreur"].includes(saving.state);
  const canSave = dur !== null && dur > 0 && dur <= maxMinutes * 60 && !working;

  /** Coupe la tranche marquée aux ciseaux, la fait extraire par CamSup puis l'enregistre sur ce PC. */
  const save = async () => {
    if (!canSave || marks.start === undefined || marks.end === undefined) return;
    const name = fileName(siteName, marks.start);
    // Chrome / Edge : choix du dossier (clé USB…) tout de suite, pendant que le clic de l'utilisateur est valable
    const picker = (window as unknown as { showSaveFilePicker?: SavePicker }).showSaveFilePicker;
    let handle: SaveHandle | null = null;
    if (picker) {
      setSaving({ state: "choix", text: "Choisissez où enregistrer le fichier…" });
      try {
        handle = await picker({ suggestedName: name, types: [{ description: "Vidéo pour les forces de l’ordre (ZIP)", accept: { "application/zip": [".zip"] } }] });
      } catch (e) {
        if ((e as Error).name === "AbortError") { setSaving(null); return; }
      }
    }
    const r = await run(() => post<{ id: number }>("/exports", { site_id: siteId, camera_ids: shown, start_ts: marks.start, end_ts: marks.end, reason }));
    if (!r) { setSaving(null); return; }
    try {
      let j: ExportJob;
      for (;;) {
        j = await api<ExportJob>(`/exports/${r.id}`);
        if (j.state === "pret" || j.state === "erreur") break;
        setSaving({ state: j.state, id: r.id, text: j.state === "en_cours" ? `Extraction : ${j.progress || "préparation"}…` : "Extraction en attente…" });
        await sleep(1500);
      }
      if (j.state === "erreur") { setSaving({ state: "erreur", id: r.id, text: `Extraction impossible : ${j.error}` }); return; }
      const url = `/api/exports/${r.id}/fichier`;
      const note = j.error ? ` Attention, ${j.error}.` : "";
      if (handle) {
        setSaving({ state: "envoi", id: r.id, text: `Enregistrement (${fmtBytes(j.size)})…` });
        const resp = await fetch(url, { credentials: "same-origin" });
        if (!resp.ok || !resp.body) throw new Error(`erreur ${resp.status}`);
        await resp.body.pipeTo(await handle.createWritable());
        setSaving({ state: "fini", id: r.id, text: `Enregistré : ${handle.name} (${fmtBytes(j.size)}).${note}` });
      } else {
        const a = document.createElement("a");
        a.href = url; a.download = name; a.click();
        setSaving({ state: "fini", id: r.id, text: `Enregistré dans le dossier Téléchargements : ${name} (${fmtBytes(j.size)}).${note}` });
      }
    } catch (e) {
      setSaving({ state: "erreur", id: r.id, text: `Enregistrement interrompu : ${(e as Error).message}` });
    }
  };
  const cut = (what: "start" | "end") => setMarks((m) => ({ ...m, [what]: at }));
  const busy = shown.some((c) => !job(c) || job(c).state === "attente" || job(c).state === "en_cours");

  return (
    <section className="card">
      <form className="form" onSubmit={show}>
        <div className="chips">{cams.map((c) => (
          <label key={c.id} className="check chip"><input type="checkbox" checked={sel.includes(c.id)} onChange={() => pick(c.id)}
            disabled={!sel.includes(c.id) && sel.length >= MAX_VIEW} /> {c.name}</label>
        ))}</div>
        <div className="grid-form form">
          <label>Jour<input type="date" required value={date} max={day(now)} onChange={(e) => setDate(e.target.value)} /></label>
          <label>De<input type="time" required value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>À<input type="time" required value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <button className="btn primary" disabled={!sel.length}>Afficher ({sel.length} caméra{sel.length > 1 ? "s" : ""})</button>
        </div>
      </form>
      {range && (
        <div className="viewer">
          <div className={`videos n${shown.length}`}>{shown.map((c) => {
            const j = job(c);
            return (
              <div key={c} className="tile">
                <div className="tile-name">{name.get(c)}</div>
                {j?.state === "pret" ? (
                  <video ref={(v) => { vids.current[c] = v; }} key={j.id} src={`/api/previews/${j.id}/video`} autoPlay={playing} muted playsInline
                         onTimeUpdate={c === master ? (e) => sync(e.currentTarget.currentTime) : undefined}
                         onEnded={c === master ? next : undefined}
                         onLoadedMetadata={(e) => {
                           const m = master !== undefined && master !== c ? vids.current[master] : null;
                           e.currentTarget.playbackRate = speed;
                           e.currentTarget.currentTime = m ? m.currentTime : startAt.current;
                         }}
                         onError={failed} onClick={toggle} />
                ) : j?.state === "erreur" ? <p className="error-text">{j.error}</p>
                  : <p className="muted"><span className="spinner" /> {mode === "copie" ? "Téléchargement depuis le NVR…" : "Téléchargement puis conversion…"}</p>}
              </div>
            );
          })}</div>
          <div className="timebar" role="slider" aria-label="Position dans la tranche horaire" aria-valuenow={at}
               aria-valuemin={range.from} aria-valuemax={range.to}
               onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); goTo(range.from + ((e.clientX - r.left) / r.width) * (range.to - range.from)); }}>
            <div className="timebar-fill" style={{ width: `${markPos(Math.min(Math.max(at, range.from), range.to), range) ?? 0}%` }} />
            {markPos(marks.start, range) !== null && <div className="cut cut-start" style={{ left: `${markPos(marks.start, range)}%` }} title="Début de l’extrait">✂</div>}
            {markPos(marks.end, range) !== null && <div className="cut cut-end" style={{ left: `${markPos(marks.end, range)}%` }} title="Fin de l’extrait">✂</div>}
          </div>
          <div className="row small muted"><span>{hms(new Date(range.from * 1000)).slice(0, 5)}</span><span className="grow" /><span>{hms(new Date(range.to * 1000)).slice(0, 5)}</span></div>
          <div className="row">
            <button className="btn small" onClick={() => goTo(at - 30)}>− 30 s</button>
            <button className="btn small" onClick={toggle} disabled={!ready.length}>{playing ? "❚❚ Pause" : "▶ Lecture"}</button>
            <button className="btn small" onClick={() => goTo(at + 30)}>+ 30 s</button>
            <b className="clock">{fmtDateTime(at)}</b>
            <span className="muted small">Vitesse</span>
            {SPEEDS.map((s) => <button key={s} className={`btn small ${speed === s ? "primary" : ""}`} onClick={() => setSpeed(s)}>×{s}</button>)}
          </div>
          <div className="row">
            <button className="btn" onClick={() => cut("start")} disabled={!ready.length}>✂ Début ici</button>
            <button className="btn" onClick={() => cut("end")} disabled={!ready.length}>✂ Fin ici</button>
            {marks.start !== undefined && <span>Début {hms(new Date(marks.start * 1000))}</span>}
            {marks.end !== undefined && <span>Fin {hms(new Date(marks.end * 1000))}</span>}
            {dur !== null && (dur <= 0 ? <Badge tone="bad">la fin est avant le début</Badge>
              : dur > maxMinutes * 60 ? <Badge tone="bad">{Math.ceil(dur / 60)} min : {maxMinutes} min au plus</Badge>
              : <Badge tone="ok">{Math.floor(dur / 60)} min {dur % 60} s</Badge>)}
          </div>
          <div className="row">
            <input className="grow" maxLength={200} placeholder="Motif ou n° de plainte (facultatif, écrit dans la fiche)" value={reason}
                   onChange={(e) => setReason(e.target.value)} aria-label="Motif ou numéro de plainte" />
            <button className="btn primary" onClick={save} disabled={!canSave}>
              ⬇ Extraire et enregistrer ({shown.length} caméra{shown.length > 1 ? "s" : ""})</button>
          </div>
          {!dur && !saving && <p className="muted small">Marquez le début et la fin aux ciseaux ({maxMinutes} min au plus) pour extraire.</p>}
          {saving && (
            <p className={saving.state === "erreur" ? "error-text" : saving.state === "fini" ? "" : "muted"}>
              {working && <span className="spinner" />} {saving.text}
              {saving.state === "fini" && saving.id && <> <a href={`/api/exports/${saving.id}/fichier`} download>Enregistrer à nouveau</a></>}
            </p>
          )}
          <p className="muted small">Cliquez sur la barre pour aller à une heure. {busy ? "Les caméras s’affichent dès que leur vidéo est arrivée. " : ""}
            Heure à quelques secondes près : l’heure exacte est celle incrustée dans l’image.
            {mode === "copie" ? " Vidéo d’origine." : " Vidéo convertie pour ce navigateur."}</p>
        </div>
      )}
    </section>
  );
}

export default function Export() {
  const ov = useApi<Overview>("/overview");
  const [siteId, setSiteId] = useState(0);
  const sites = useMemo(() => (ov.data?.sites ?? []).filter((s) => ov.data?.cameras.some((c) => c.site_id === s.id && c.brand === "dahua")), [ov.data]);
  const site = siteId || sites[0]?.id || 0;
  const cams = useMemo(() => (ov.data?.cameras ?? []).filter((c) => c.site_id === site && c.brand === "dahua"), [ov.data, site]);

  if (ov.loading && !ov.data) return <Loading />;
  if (ov.error && !ov.data) return <ErrorState message={ov.error} onRetry={ov.reload} />;
  return (
    <>
      <PageHeader title="Voir la vidéo">
        {sites.length > 1 && (
          <select value={site} onChange={(e) => setSiteId(Number(e.target.value))} aria-label="Pharmacie">
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        )}
      </PageHeader>
      <Notice>Choisissez les caméras et la tranche horaire, regardez, coupez aux ciseaux le début et la fin, puis
        <b> Extraire et enregistrer</b> : un fichier ZIP à remettre tel quel aux gendarmes ou à la police (clé USB). Il
        contient une vidéo MP4 par caméra (copie sans perte de l’enregistrement du NVR) et une fiche <b>LISEZ-MOI</b>
        (pharmacie, période, empreintes de contrôle). Lecture conseillée : VLC.</Notice>
      {!cams.length ? <Empty>Aucune caméra derrière un NVR Dahua.</Empty>
        : <Viewer key={site} cams={cams} siteId={site} siteName={sites.find((s) => s.id === site)?.name ?? ""}
                  maxMinutes={ov.data?.export_max_minutes ?? 15} />}
    </>
  );
}
