/** Une seule connexion WebSocket pour tout le mur : le serveur envoie chaque image dès qu'elle arrive (plus d'attente
 *  entre deux demandes). Chaque tuile s'abonne à sa caméra ; la connexion est refaite quand la liste change. */
type Cb = (url: string | null, error?: string) => void;
const subs = new Map<number, Set<Cb>>();
let ws: WebSocket | null = null;
let key = "";
let timer: ReturnType<typeof setTimeout> | undefined;
let fails = 0;

function connect() {
  const ids = [...subs.keys()].sort((a, b) => a - b);
  const k = ids.join(",");
  if (k === key && ws && ws.readyState <= WebSocket.OPEN) return;
  const old = ws;
  ws = null; key = k;
  old?.close();
  if (!ids.length) return;
  const s = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live/ws?ids=${k}`);
  s.binaryType = "arraybuffer";
  s.onopen = () => { fails = 0; };
  s.onmessage = (e) => {
    if (typeof e.data === "string") {
      const m = JSON.parse(e.data) as { id: number; error: string };
      subs.get(m.id)?.forEach((cb) => cb(null, m.error));
      return;
    }
    const id = new DataView(e.data as ArrayBuffer).getUint32(0);
    const url = URL.createObjectURL(new Blob([(e.data as ArrayBuffer).slice(4)], { type: "image/jpeg" }));
    const listeners = subs.get(id);
    if (!listeners?.size) { URL.revokeObjectURL(url); return; }
    listeners.forEach((cb) => cb(url));
  };
  s.onclose = () => {
    if (ws !== s) return;                      // remplacée volontairement
    ws = null; key = ""; fails++;
    timer = setTimeout(connect, Math.min(10000, 1000 * fails));
  };
  ws = s;
}

function schedule() { clearTimeout(timer); timer = setTimeout(connect, 50); }

export function subscribe(id: number, cb: Cb): () => void {
  if (!subs.has(id)) subs.set(id, new Set());
  subs.get(id)!.add(cb);
  schedule();
  return () => {
    const set = subs.get(id);
    set?.delete(cb);
    if (set && !set.size) subs.delete(id);
    schedule();
  };
}

/** La connexion échoue à répétition (réseau, pare-feu) : les tuiles passent aux demandes d'images. */
export const hubFailing = () => fails >= 3;
