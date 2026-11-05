import { useEffect, useRef, useState } from "react";

/** Image en direct d'une caméra pour le mur : dernière image demandée plusieurs fois par seconde (requêtes courtes ;
 *  un flux continu par caméra épuiserait les 6 connexions qu'un navigateur ouvre à la fois). En pause quand la page
 *  est cachée. `onState` : « ok » quand des images arrivent, « erreur » (message) sinon. */
export function LiveImage({ camId, fps = 3, onState }: { camId: number; fps?: number; onState?: (s: "attente" | "ok" | "erreur", msg?: string) => void }) {
  const [src, setSrc] = useState<string | null>(null);
  const last = useRef<string | null>(null);
  useEffect(() => {
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
    return () => { live = false; clearTimeout(timer); if (last.current) URL.revokeObjectURL(last.current); last.current = null; };
  }, [camId, fps]); // onState : rappel de la tuile, pas une dépendance
  return src ? <img className="wtile-live" src={src} alt="" /> : null;
}
