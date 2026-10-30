/** Accès à l'API CamSup : session par cookie, en-tête anti-CSRF, erreurs normalisées. */

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Émis quand la session a expiré : l'application réaffiche l'écran de connexion. */
export const AUTH_EXPIRED = "camsup:auth-expired";

type Options = { method?: string; body?: unknown; signal?: AbortSignal };

export async function api<T>(path: string, opt: Options = {}): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`/api${path}`, {
      method: opt.method ?? "GET",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CamSup": "1" },
      body: opt.body === undefined ? undefined : JSON.stringify(opt.body),
      signal: opt.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Serveur injoignable : vérifiez la connexion réseau");
  }
  if (r.status === 401 && path !== "/login") {
    window.dispatchEvent(new Event(AUTH_EXPIRED));
    throw new ApiError(401, "Session expirée : reconnectez-vous");
  }
  if (!r.ok) {
    let message = r.statusText || `Erreur ${r.status}`;
    try {
      const j = await r.json();
      if (typeof j.detail === "string") message = j.detail;
      else if (Array.isArray(j.detail)) message = j.detail.map((d: { msg: string }) => d.msg).join(" ; ");
    } catch {
      /* corps non JSON : on garde le statut HTTP */
    }
    throw new ApiError(r.status, message);
  }
  return (await r.json()) as T;
}

export const post = <T>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ?? {} });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });
