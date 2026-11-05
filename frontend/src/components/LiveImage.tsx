import { useEffect, useRef, useState } from "react";
import { hubFailing, subscribe } from "./liveHub";

/** Image en direct d'une caméra pour le mur : images poussées par le serveur dès leur arrivée (`liveHub`, une seule
 *  connexion pour tout le mur, retard minimal) ; si cette connexion échoue à répétition, repli sur des demandes
 *  d'images répétées. `onState` : « ok » quand des images arrivent, « erreur » (message) sinon. */
export function LiveImage({ camId, fps = 6, onState }: { camId: number; fps?: number; onState?: (s: "attente" | "ok" | "erreur", msg?: string) => void }) {
  const [src, setSrc] = useState<string | null>(null);
  const last = useRef<string | null>(null);
  const [polling, setPolling] = useState(false);
  // images poussées par le serveur
  useEffect(() => {
    if (polling) return;
    let gotOne = false;
    const unsub = subscribe(camId, (url, error) => {
      if (error) { onState?.("erreur", error); return; }
      if (!url) return;
      const prev = last.current;
      last.current = url; setSrc(url); gotOne = true; onState?.("ok");
      if (prev) setTimeout(() => URL.revokeObjectURL(prev), 1000);   // l'image affichée reste valable le temps du changement
    });
    const watch = setInterval(() => { if (!gotOne && hubFailing()) setPolling(true); }, 3000);
    return () => { unsub(); clearInterval(watch); };
  }, [camId, polling]); // onState : rappel de la tuile
  // repli : demandes d'images répétées
  useEffect(() => {
    if (!polling) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    let fails = 0;
    const tick = async () => {
      if (!live) return;
      if (document.hidden) { timer = setTimeout(tick, 1000); return; }
      try {
        const r = await fetch(`/api/cameras/${camId}/image`, { credentials: "same-origin", cache: "no-store" });
        if (r.status === 200) {
          const url = URL.createObjectURL(await r.blob());
          if (!live) { URL.revokeObjectURL(url); return; }
          if (last.current) URL.revokeObjectURL(last.current);
          last.current = url; setSrc(url); fails = 0; onState?.("ok");
        } else if (r.status === 202) {
          onState?.("attente");
        } else {
          fails++;
          const detail = (await r.json().catch(() => null))?.detail;
          onState?.("erreur", detail ?? `erreur ${r.status}`);
        }
      } catch { fails++; onState?.("erreur", "CamSup ne répond pas"); }
      timer = setTimeout(tick, fails ? Math.min(10000, 2000 * fails) : 1000 / fps);
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [camId, fps, polling]); // onState : rappel de la tuile, pas une dépendance
  useEffect(() => () => { if (last.current) URL.revokeObjectURL(last.current); last.current = null; }, []);
  return src ? <img className="wtile-live" src={src} alt="" /> : null;
}
