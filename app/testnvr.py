"""Test pas à pas de la lecture d'un NVR Dahua, pour l'assistance : `python -m app.testnvr [adresse]`.

Reprend les identifiants enregistrés dans CamSup (rien à saisir, aucun mot de passe affiché) et refait chaque étape de
la surveillance une par une (connexion, heure, état des caméras, recherche des enregistrements de chaque canal), avec
sa durée et l'erreur exacte. À lancer pendant que CamSup tourne ou non.
"""
import sys
import time
import traceback
from datetime import datetime, timedelta

from . import dahuarpc, db, devicehttp, nvrwatch, security


def step(name: str, fn):
    t = time.time()
    try:
        r = fn()
        print(f"  OK   {name} ({time.time() - t:.2f} s)")
        return r
    except Exception as e:  # noqa: BLE001
        print(f"  ÉCHEC {name} ({time.time() - t:.2f} s) : {type(e).__name__} : {security.scrub(str(e))[:300]}")
        traceback.print_exc(limit=4)
        return None


def main() -> None:
    db.init()
    hosts = db.q("SELECT DISTINCT host, port_http, username, password_enc FROM cameras WHERE brand='dahua'")
    if len(sys.argv) > 1:
        hosts = [h for h in hosts if h["host"] == sys.argv[1]]
    if not hosts:
        sys.exit("Aucun NVR Dahua enregistré dans CamSup (ou adresse inconnue)")
    for h in hosts:
        host, port, user = h["host"], h["port_http"], h["username"]
        pw = security.decrypt(h["password_enc"])
        chans = sorted({r["channel"] for r in db.q("SELECT channel FROM cameras WHERE host=? AND brand='dahua'", (host,))})
        print(f"NVR {host} (port {port}, utilisateur {user}, canaux {chans})")
        print(f"  port HTTP noté en panne : {devicehttp.http_dead(host)}")
        s = dahuarpc.Session(host, port, user, pw, timeout=10)
        if step("adresse de l'API (HTTP ou repli HTTPS)", lambda: setattr(s, "base", s._base()) or s.base) is None:
            continue
        print(f"       → {s.base}")
        if step("connexion (RPC2_Login)", lambda: s.__enter__()) is None:
            continue
        try:
            t = step("heure du NVR", lambda: s.params("global.getCurrentTime")["time"])
            nvr_now = datetime.strptime(t, nvrwatch.FMT) if t else datetime.now()
            if t:
                print(f"       → {t} (écart avec ce PC : {(nvr_now - datetime.now()).total_seconds():+.0f} s)")
            states = step("état des caméras", lambda: s.params("LogicDeviceManager.getCameraState", {"uniqueChannels": [-1]}).get("states"))
            if states:
                print("       → " + ", ".join(f"canal {int(x['channel']) + 1} : {x.get('connectionState')}" for x in states[:16]))
            for ch in chans:
                files = step(f"canal {ch} : enregistrements des 10 dernières minutes",
                             lambda: nvrwatch._files(s, ch, nvr_now - timedelta(minutes=10), nvr_now))
                if files is not None:
                    print(f"       → {len(files)} fichier(s){'' if not files else ', fin du dernier : ' + max(b for _, b in files)}")
                files = step(f"canal {ch} : enregistrements des 24 dernières heures",
                             lambda: nvrwatch._files(s, ch, nvr_now - timedelta(hours=24), nvr_now))
                if files is not None:
                    print(f"       → {len(files)} fichier(s)")
            step("relevé complet, comme la surveillance", lambda: nvrwatch.probe(host, port, user, pw, chans, timeout=10))
        finally:
            s.__exit__()


if __name__ == "__main__":
    main()
