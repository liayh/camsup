"""Fabrique un code de licence CamSup (côté vendeur seulement).

    python3 outils/creer_licence.py                 → code valable 1 an à partir d'aujourd'hui
    python3 outils/creer_licence.py --fin 2027-12-31 --numero 12

Il faut la clé privée `licence-privee.key` (à la racine du projet, ou --cle CHEMIN). Ne jamais la mettre sur GitHub
ni dans un installateur : avec elle, n'importe qui fabrique des licences. Le code s'entre à l'installation de
CamSup, ou dans Paramètres › Licence pour renouveler.
"""
import argparse
import base64
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from app import licence  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Code de licence CamSup")
    ap.add_argument("--fin", help="dernier jour de validité, AAAA-MM-JJ (défaut : dans 1 an)")
    ap.add_argument("--numero", type=int, help="numéro de licence (défaut : tiré de l'heure)")
    ap.add_argument("--cle", default=str(Path(__file__).resolve().parent.parent / "licence-privee.key"))
    a = ap.parse_args()
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(Path(a.cle).read_text().strip()))
    today = date.today()
    if a.fin:
        fin = date.fromisoformat(a.fin)
    else:
        try:
            fin = today.replace(year=today.year + 1)
        except ValueError:                                # 29 février
            fin = today + timedelta(days=365)
    numero = a.numero if a.numero is not None else int(time.time()) % 1_000_000_000
    code = licence.encode(numero, fin, key.sign)
    assert licence.decode(code)["fin"] == fin           # vérifié avec la clé publique de CamSup
    print(f"Licence n° {numero}, valable jusqu'au {fin:%d/%m/%Y} inclus :\n\n{code}\n")


if __name__ == "__main__":
    main()
