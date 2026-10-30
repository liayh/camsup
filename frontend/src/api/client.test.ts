import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, AUTH_EXPIRED, post } from "./client";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(status: number, body: unknown) {
  const f = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", f);
  return f;
}

describe("client API", () => {
  it("envoie l'en-tête anti-CSRF et le cookie de session", async () => {
    const f = stubFetch(200, { ok: true });
    await post("/x", { a: 1 });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/x");
    expect(init.headers["X-CamSup"]).toBe("1");
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBe('{"a":1}');
  });
  it("session expirée : erreur 401 et événement pour réafficher la connexion", async () => {
    stubFetch(401, { detail: "Authentification requise" });
    const seen = vi.fn();
    window.addEventListener(AUTH_EXPIRED, seen);
    await expect(api("/overview")).rejects.toMatchObject({ status: 401 });
    expect(seen).toHaveBeenCalledOnce();
  });
  it("reprend le message d'erreur du serveur", async () => {
    stubFetch(409, { detail: "Ce client a encore des sites" });
    await expect(api("/clients/1")).rejects.toThrow("Ce client a encore des sites");
  });
  it("erreurs de validation : messages réunis", async () => {
    stubFetch(422, { detail: [{ msg: "champ requis" }, { msg: "trop long" }] });
    await expect(api("/x")).rejects.toThrow("champ requis ; trop long");
  });
  it("serveur injoignable : message explicite", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const e = (await api("/x").catch((x: unknown) => x)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect(e.message).toMatch(/injoignable/);
  });
});
