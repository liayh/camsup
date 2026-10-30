import { createContext, ReactNode, useCallback, useContext, useState } from "react";

type Tone = "ok" | "bad" | "info";
interface ToastItem { id: number; text: string; tone: Tone }
const Ctx = createContext<(text: string, tone?: Tone) => void>(() => undefined);

/** Retour des actions (enregistré, erreur…) — gestion centralisée, affichée en bas à droite. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((text: string, tone: Tone = "info") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x.slice(-4), { id, text, tone }]);
    window.setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "bad" ? 8000 : 4000);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast t-${t.tone}`}>{t.text}</div>)}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);

/** Exécute une action et affiche le résultat (succès ou message d'erreur du serveur). */
export function useAction() {
  const toast = useToast();
  return useCallback(async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    try {
      const r = await fn();
      if (ok) toast(ok, "ok");
      return r;
    } catch (e) {
      toast((e as Error).message, "bad");
      return undefined;
    }
  }, [toast]);
}
