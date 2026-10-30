"""Base et dossiers de test isolés : à importer avant toute partie de l'application."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="camsup-test-")
os.environ.update(DATA_DIR=_TMP, ADMIN_USER="admin", ADMIN_PASSWORD="motdepasse-admin",
                  CAMSUP_ENV=os.path.join(_TMP, "absent.env"))   # jamais le .env réel (e-mails envoyés)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
