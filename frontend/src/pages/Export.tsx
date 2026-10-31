import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, post } from "../api/client";
import type { Camera, ExportJob, Preview } from "../api/types";
import { useAction, useToast } from "../components/Toast";
import { Badge, Empty, ErrorState, Loading, Notice, PageHeader } from "../components/ui";
import { fmtBytes, fmtDateTime } from "../format";
import { useCurrent } from "../components/SiteContext";

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
  if (ts === undefined || Number.isNaN(ts) || ts < r.from || ts > r.to) return null;
  return ((ts - r.from) / (r.to - r.from)) * 100;
}

/** Périodes enregistrées [début, fin] (s) ; `null` = inconnues (NVR non interrogé) : tout est demandé. */
export type Segs = number[][];
/** Périodes enregistrées par au moins une des caméras, triées et fusionnées. */
export function union(rec: Record<string, Segs>, cams: number[]): Segs {
  const all = cams.flatMap((c) => rec[c] ?? []).map(([a, b]) => [a, b]).sort((x, y) => x[0] - y[0]);
  const out: Segs = [];
  for (const [a, b] of all) {
    if (out.length && a <= out[out.length - 1][1] + 2) out[out.length - 1][1] = Math.max(out[out.length - 1][1], b);
    else out.push([a, b]);
  }
  return out;
}
/** Premier instant enregistré dans [from, to[, ou null. */
export function firstRecorded(segs: Segs, from: number, to: number): number | null {
  for (const [a, b] of segs) if (b > from && a < to) return Math.max(a, from);
  return null;
}
const hm = (ts: number) => hms(new Date(ts * 1000)).slice(0, 5);
const dm = (ts: number) => { const d = new Date(ts * 1000); return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`; };
/** « 30/10 13:00 → 19:34, 31/10 09:50 → 12:54 » */
export const fmtSegs = (segs: Segs) =>
  segs.map(([a, b]) => `${dm(a)} ${hm(a)} → ${dm(a) === dm(b) ? "" : dm(b) + " "}${hm(b)}`).join(", ");

/** Secondes enregistrées (par au moins une caméra) dans [a, b[. */
export function covered(segs: Segs, a: number, b: number): number {
  return segs.reduce((n, [x, y]) => n + Math.max(0, Math.min(y, b) - Math.max(x, a)), 0);
}
/** « 5 min », « 4 min 37 s » */
export const fmtDur = (s: number) => `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;

/** Sauvegarde (début + durée de 1 à 15 min, extraite directement) et lecture d'une tranche horaire pour trouver le
 *  moment : 1 à 4 caméras synchronisées, ciseaux qui règlent le début et la durée de la sauvegarde. */
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
  const [startTime, setStartTime] = useState(hms(new Date(now.getTime() - 900000)));   // début de la sauvegarde
  const [dur, setDur] = useState(300);                                                   // durée (s)
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState<Saving | null>(null);
  const [rec, setRec] = useState<Record<string, Segs> | null>(null);       // périodes enregistrées ce jour-là
  const [looking, setLooking] = useState(false);
  const recFor = useRef<{ key: string; p: Promise<Record<string, Segs> | null> } | null>(null);
  const vids = useRef<Record<number, HTMLVideoElement | null>>({});
  const asked = useRef<Set<string>>(new Set());
  const startAt = useRef(0);
  const name = useMemo(() => new Map(cams.map((c) => [c.id, c.name])), [cams]);
  const base = range ? rangeStart(range.from) : 0;
  const chunks = range ? Math.ceil((range.to - base) / CHUNK) : 0;
  const span = (k: number): [number, number] => [base + k * CHUNK, base + (k + 1) * CHUNK];
  /** La caméra a-t-elle des images dans le morceau k ? (inconnu → oui) */
  const has = (cam: number, k: number) => !rec || !(cam in rec) || firstRecorded(rec[cam], ...span(k)) !== null;
  /** Décalage (s) du début de la vidéo d'un morceau qui commence dans un trou : le NVR n'envoie que ce qui existe. */
  const lead = (cam: number, k: number) => {
    const f = rec && cam in rec ? firstRecorded(rec[cam], ...span(k)) : null;
    return f === null ? 0 : f - span(k)[0];
  };
  const recorded = useMemo(() => (rec ? union(rec, shown) : null), [rec, shown]);
  const bStart = new Date(`${date}T${startTime.length === 5 ? startTime + ":00" : startTime}`).getTime() / 1000;
  const bEnd = bStart + dur;

  /** Ce que le NVR a enregistré le jour choisi (et la nuit suivante), pour les caméras cochées ; une demande par choix. */
  const fetchRec = (d: string, ids: number[]) => {
    const key = `${d}|${[...ids].sort().join(",")}`;
    if (recFor.current?.key !== key) {
      const dayStart = new Date(`${d}T00:00`).getTime() / 1000;
      const end = Math.min(dayStart + 30 * 3600, Math.floor(Date.now() / 1000));
      recFor.current = { key, p: end <= dayStart ? Promise.resolve({}) : post<Record<string, Segs>>("/recordings", { camera_ids: ids, start_ts: dayStart, end_ts: end })
        .catch((err) => { toast(`Périodes enregistrées inconnues : ${(err as Error).message}`, "info"); recFor.current = null; return null; }) };
    }
    return recFor.current.p;
  };
  useEffect(() => {
    if (!sel.length || !date) return;
    let live = true;
    fetchRec(date, sel).then((r) => { if (live) setRec(r); });
    return () => { live = false; };
  }, [date, sel]); // fetchRec : même résultat pour un même jour et les mêmes caméras

  // morceau en cours et le suivant (lecture sans attente), pour chaque caméra affichée
  useEffect(() => {
    if (!range) return;
    for (const k of [chunk, chunk + 1].filter((k) => k < chunks)) {
      for (const cam of shown.filter((c) => has(c, k))) {
        const key = `${mode}-${k}-${cam}`;
        if (asked.current.has(key)) continue;
        asked.current.add(key);
        run(() => post<Preview>("/previews", { camera_id: cam, start_ts: base + k * CHUNK, mode }))
          .then((r) => { if (r) setJobs((j) => ({ ...j, [key]: r })); else asked.current.delete(key); });
      }
    }
  }, [range, chunk, shown, mode, chunks, base, run, rec]);

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
  const ready = shown.filter((c) => has(c, chunk) && job(c)?.state === "pret");
  const master = ready[0];
  const all = () => ready.map((c) => vids.current[c]).filter((v): v is HTMLVideoElement => !!v);
  const at = base + chunk * CHUNK + Math.floor(pos);

  /** Lecture de [from, to] (heure du PC) pour les caméras cochées. */
  const display = async (want: Range) => {
    const r = { from: want.from, to: Math.min(want.to, Math.floor(Date.now() / 1000)) };
    if (!sel.length) return;
    if (r.to <= r.from) { toast("Cette période n’est pas encore enregistrée", "bad"); return; }
    if (r.to - r.from > MAX_HOURS * 3600) { toast(`${MAX_HOURS} h au plus d’un coup`, "bad"); return; }
    setLooking(true);
    const found = await fetchRec(date, sel);      // pour la barre de temps et pour sauter les trous
    setLooking(false);
    const first = found ? firstRecorded(union(found, sel), r.from, r.to) : r.from;
    const [k, off] = locate(first ?? r.from, r);
    startAt.current = off;
    asked.current.clear(); setJobs({});
    setRec(found); setRange(r); setShown(sel); setChunk(k); setPos(off); setPlaying(true);
  };
  const show = (e: FormEvent) => {
    e.preventDefault();
    const p = period(date, from, to);
    if (p) display({ from: p[0], to: p[1] });
  };
  const goTo = (ts: number) => {
    if (!range) return;
    const f = recorded ? firstRecorded(recorded, ts, range.to) : null;      // dans un trou : moment enregistré suivant
    const [k, off] = locate(f ?? ts, range);
    setPos(off);
    if (k === chunk) { for (const c of ready) { const v = vids.current[c]; if (v) v.currentTime = Math.max(0, off - lead(c, k)); } return; }
    startAt.current = off;
    setChunk(k);
  };
  const sync = (t: number) => {
    setPos(t);
    if (range && base + chunk * CHUNK + t >= range.to) { for (const v of all()) v.pause(); setPlaying(false); return; }
    for (const c of ready) {
      const v = vids.current[c], want = Math.max(0, t - lead(c, chunk));
      if (v && c !== master && Math.abs(v.currentTime - want) > 0.5) v.currentTime = want;
    }
  };
  /** Morceau suivant qui contient des images (les trous d'enregistrement sont sautés). */
  const next = () => {
    let k = chunk + 1;
    while (k < chunks && !shown.some((c) => has(c, k))) k++;
    if (k >= chunks) { setPlaying(false); return; }
    const f = recorded ? firstRecorded(recorded, ...span(k)) : null;
    const off = f === null ? 0 : f - span(k)[0];
    startAt.current = off; setPos(off); setChunk(k);
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
  const working = !!saving && !["fini", "erreur"].includes(saving.state);
  const nowTs = Math.floor(Date.now() / 1000);
  const canSave = sel.length > 0 && !Number.isNaN(bStart) && bStart < nowTs && dur >= 60 && dur <= maxMinutes * 60 && !working;
  const segs = rec ? union(rec, sel) : null;
  const got = segs && !Number.isNaN(bStart) ? covered(segs, bStart, Math.min(bEnd, nowTs)) : null;

  /** Fait extraire la période par CamSup puis l'enregistre sur ce PC. */
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSave) return;
    const name = fileName(siteName, bStart);
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
    const r = await run(() => post<{ id: number }>("/exports", { site_id: siteId, camera_ids: sel, start_ts: bStart, end_ts: bEnd, reason }));
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
  /** Ciseaux : début de la sauvegarde à l'image affichée ; fin = durée depuis ce début. */
  const cutStart = () => { const d = new Date(at * 1000); setDate(day(d)); setStartTime(hms(d)); setSaving(null); };
  const cutEnd = () => {
    const d = at - bStart;
    if (Number.isNaN(d) || d < 60) { toast("La fin doit être au moins 1 min après le début de la sauvegarde", "bad"); return; }
    if (d > maxMinutes * 60) { toast(`${maxMinutes} min au plus : avancez le début (✂ Début ici)`, "bad"); return; }
    setDur(d); setSaving(null);
  };
  const busy = shown.some((c) => has(c, chunk) && (!job(c) || job(c).state === "attente" || job(c).state === "en_cours"));
  const inRange = recorded && range ? recorded.filter(([a, b]) => b > range.from && a < range.to) : null;

  const minutes = Array.from({ length: maxMinutes }, (_, i) => (i + 1) * 60);
  return (
    <>
    <section className="card">
      <h2>Sauvegarde</h2>
      <form className="form" onSubmit={save}>
        <div className="chips">{cams.map((c) => (
          <label key={c.id} className="check chip"><input type="checkbox" checked={sel.includes(c.id)} onChange={() => pick(c.id)}
            disabled={!sel.includes(c.id) && sel.length >= MAX_VIEW} /> {c.name}</label>
        ))}</div>
        <div className="grid-form form">
          <label>Jour<input type="date" required value={date} max={day(now)} onChange={(e) => setDate(e.target.value)} /></label>
          <label>Début<input type="time" step={1} required value={startTime} onChange={(e) => setStartTime(e.target.value)} /></label>
          <label>Durée<select value={dur} onChange={(e) => setDur(Number(e.target.value))}>
            {!minutes.includes(dur) && <option value={dur}>{fmtDur(dur)}</option>}
            {minutes.map((m) => <option key={m} value={m}>{m / 60} min</option>)}</select></label>
          <label>Fin<input readOnly value={Number.isNaN(bEnd) ? "" : hms(new Date(bEnd * 1000))} tabIndex={-1} /></label>
        </div>
        {got !== null && (got >= Math.min(bEnd, nowTs) - bStart - 2 ? <p className="small"><Badge tone="ok">images enregistrées</Badge> sur toute la période</p>
          : got > 0 ? <p className="small"><Badge tone="warn">en partie</Badge> le NVR n’a des images que sur {fmtDur(Math.round(got))} de cette période</p>
          : <p className="small"><Badge tone="bad">aucune image</Badge> le NVR n’a rien enregistré sur cette période
            {segs?.length ? <> (enregistré ce jour-là : {fmtSegs(segs)})</> : ""}</p>)}
        <div className="row">
          <input className="grow" maxLength={200} placeholder="Motif ou n° de plainte (facultatif, écrit dans la fiche)" value={reason}
                 onChange={(e) => setReason(e.target.value)} aria-label="Motif ou numéro de plainte" />
          <button type="button" className="btn" disabled={!sel.length || looking || Number.isNaN(bStart)}
                  onClick={() => display({ from: bStart, to: bEnd })}>▶ Voir cette période</button>
          <button className="btn primary" disabled={!canSave}>⬇ Extraire et enregistrer ({sel.length} caméra{sel.length > 1 ? "s" : ""})</button>
        </div>
        {saving && (
          <p className={saving.state === "erreur" ? "error-text" : saving.state === "fini" ? "" : "muted"}>
            {working && <span className="spinner" />} {saving.text}
            {saving.state === "fini" && saving.id && <> <a href={`/api/exports/${saving.id}/fichier`} download>Enregistrer à nouveau</a></>}
          </p>
        )}
      </form>
    </section>
    <section className="card">
      <h2>Chercher le moment</h2>
      <form className="form" onSubmit={show}>
        <div className="grid-form form">
          <label>De<input type="time" required value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>À<input type="time" required value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <button className="btn" disabled={!sel.length || looking}>
            {looking ? <><span className="spinner" /> Recherche sur le NVR…</> : <>Afficher ({sel.length} caméra{sel.length > 1 ? "s" : ""})</>}</button>
        </div>
      </form>
      {range && inRange?.length === 0 && (
        <Notice tone="warn">Le NVR n’a aucune image de {shown.length > 1 ? "ces caméras" : "cette caméra"} entre {hm(range.from)} et {hm(range.to)}
          {" "}(caméra débranchée, coupure de courant ou NVR arrêté ?).
          {recorded?.length ? <> Enregistré ce jour-là : <b>{fmtSegs(recorded)}</b>.</> : " Rien d’enregistré ce jour-là."}</Notice>
      )}
      {range && inRange?.length !== 0 && (
        <div className="viewer">
          <div className={`videos n${shown.length}`}>{shown.map((c) => {
            const j = job(c);
            const L = lead(c, chunk);
            return (
              <div key={c} className="tile">
                <div className="tile-name">{name.get(c)}</div>
                {j?.state === "pret" ? (
                  <video ref={(v) => { vids.current[c] = v; }} key={j.id} src={`/api/previews/${j.id}/video`} autoPlay={playing} muted playsInline
                         onTimeUpdate={c === master ? (e) => sync(e.currentTarget.currentTime + L) : undefined}
                         onEnded={c === master ? next : undefined}
                         onLoadedMetadata={(e) => {
                           const m = master !== undefined && master !== c ? vids.current[master] : null;
                           e.currentTarget.playbackRate = speed;
                           e.currentTarget.currentTime = Math.max(0, (m ? m.currentTime + lead(master!, chunk) : startAt.current) - L);
                         }}
                         onError={failed} onClick={toggle} />
                ) : !has(c, chunk) ? <p className="muted">Pas d’image enregistrée à ce moment</p>
                  : j?.state === "erreur" ? <p className="error-text">{j.error}</p>
                  : <p className="muted"><span className="spinner" /> {mode === "copie" ? "Téléchargement depuis le NVR…" : "Téléchargement puis conversion…"}</p>}
              </div>
            );
          })}</div>
          <div className="timebar" role="slider" aria-label="Position dans la tranche horaire" aria-valuenow={at}
               aria-valuemin={range.from} aria-valuemax={range.to}
               onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); goTo(range.from + ((e.clientX - r.left) / r.width) * (range.to - range.from)); }}>
            {inRange?.map(([a, b]) => (
              <div key={a} className="timebar-rec" style={{ left: `${markPos(Math.max(a, range.from), range)}%`, right: `${100 - (markPos(Math.min(b, range.to), range) ?? 100)}%` }} />
            ))}
            <div className="timebar-fill" style={{ width: `${markPos(Math.min(Math.max(at, range.from), range.to), range) ?? 0}%` }} />
            {markPos(bStart, range) !== null && <div className="cut cut-start" style={{ left: `${markPos(bStart, range)}%` }} title="Début de la sauvegarde">✂</div>}
            {markPos(bEnd, range) !== null && <div className="cut cut-end" style={{ left: `${markPos(bEnd, range)}%` }} title="Fin de la sauvegarde">✂</div>}
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
            <button className="btn" onClick={cutStart} disabled={!ready.length}>✂ Début ici</button>
            <button className="btn" onClick={cutEnd} disabled={!ready.length}>✂ Fin ici</button>
            <span className="muted small">règlent le début et la durée de la sauvegarde (ci-dessus)</span>
          </div>
          <p className="muted small">Cliquez sur la barre pour aller à une heure{inRange ? " (en vert : périodes enregistrées ; les trous sont sautés)" : ""}. {busy ? "Les caméras s’affichent dès que leur vidéo est arrivée. " : ""}
            Heure à quelques secondes près : l’heure exacte est celle incrustée dans l’image.
            {mode === "copie" ? " Vidéo d’origine." : " Vidéo convertie pour ce navigateur."}</p>
        </div>
      )}
    </section>
    </>
  );
}

export default function Export() {
  const { ov, site, name, cams: all, loading, error, reload } = useCurrent();
  const cams = useMemo(() => all.filter((c) => c.brand === "dahua"), [all]);
  if (loading && !ov) return <Loading />;
  if (error && !ov) return <ErrorState message={error} onRetry={reload} />;
  return (
    <>
      <PageHeader title="Vidéo" sub={name} />
      <Notice>Choisissez les caméras, le début et la durée (1 à {ov?.export_max_minutes ?? 15} min), puis
        <b> Extraire et enregistrer</b>. Heure inconnue : <b>Chercher le moment</b>, regardez, et réglez la sauvegarde aux
        ciseaux. Le résultat est un fichier ZIP à remettre tel quel aux gendarmes ou à la police (clé USB). Il
        contient une vidéo MP4 par caméra (copie sans perte de l’enregistrement du NVR) et une fiche <b>LISEZ-MOI</b>
        (pharmacie, période, empreintes de contrôle). Lecture conseillée : VLC.</Notice>
      {!cams.length ? <Empty>Aucune caméra derrière un NVR Dahua.</Empty>
        : <Viewer key={site} cams={cams} siteId={site} siteName={name} maxMinutes={ov?.export_max_minutes ?? 15} />}
    </>
  );
}
