"""SQLite minimaliste, thread-safe."""
import sqlite3
import threading
import time

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites(             -- une pharmacie
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cameras(           -- un canal d'un enregistreur (NVR)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  brand TEXT NOT NULL,              -- hikvision | dahua
  host TEXT NOT NULL,               -- adresse de l'enregistreur
  port_http INTEGER NOT NULL DEFAULT 80,
  username TEXT NOT NULL DEFAULT '',
  password_enc TEXT NOT NULL DEFAULT '',
  channel INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL,
  camera_id INTEGER,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  opened_ts INTEGER NOT NULL,
  closed_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alert_key ON alerts(key, closed_ts);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL,               -- integrator (tous les sites) | pharmacist (sites attribués)
  active INTEGER NOT NULL DEFAULT 1,
  created_ts INTEGER NOT NULL,
  last_login_ts INTEGER
);
CREATE TABLE IF NOT EXISTS user_sites(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  PRIMARY KEY(user_id, site_id)
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY,      -- SHA-256 du jeton (le jeton lui-même n'est jamais stocké)
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_ts INTEGER NOT NULL,
  expires_ts INTEGER NOT NULL,
  ip TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  user_id INTEGER,
  username TEXT NOT NULL DEFAULT '',
  site_id INTEGER,
  action TEXT NOT NULL,
  object TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  ip TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
CREATE TABLE IF NOT EXISTS smart_baseline(    -- compteurs SMART au premier relevé de chaque disque (tendance)
  host TEXT NOT NULL, serial TEXT NOT NULL, attr INTEGER NOT NULL, raw INTEGER NOT NULL, ts INTEGER NOT NULL,
  PRIMARY KEY(host, serial, attr)
);
CREATE TABLE IF NOT EXISTS exports(           -- extraits vidéo à remettre aux forces de l'ordre (`export`)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  user_id INTEGER,
  username TEXT NOT NULL DEFAULT '',
  camera_ids TEXT NOT NULL,         -- liste JSON
  cameras TEXT NOT NULL DEFAULT '', -- noms au moment de la demande
  start_ts INTEGER NOT NULL,        -- période demandée (heure du PC)
  end_ts INTEGER NOT NULL,
  reason TEXT DEFAULT '',           -- motif, n° de plainte
  state TEXT NOT NULL,              -- attente | en_cours | pret | erreur
  progress TEXT DEFAULT '',
  error TEXT DEFAULT '',
  size INTEGER,
  sha256 TEXT DEFAULT '',
  created_ts INTEGER NOT NULL,
  done_ts INTEGER
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status(            -- dernier état connu de chaque caméra
  camera_id INTEGER PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
  online INTEGER NOT NULL DEFAULT 0,
  last_check INTEGER,
  last_ok INTEGER,
  error TEXT DEFAULT ''
);
"""

# Tables des fonctions retirées (enregistrement, vidéo, détection de vol, multi-clients, incidents) :
# supprimées des bases existantes, enfants avant parents (clés étrangères).
OBSOLETE = ("segments", "events", "theft_review", "theft_zones", "theft_settings", "incident_notes", "incident_alerts",
            "incidents", "state_log", "user_clients", "clients")


def init() -> None:
    global _conn
    with _lock:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _drop_obsolete()
        _conn.execute("UPDATE users SET role='pharmacist' WHERE role NOT IN ('integrator','pharmacist')")
        _conn.commit()


def _drop_obsolete() -> None:
    """Retire les tables abandonnées et la colonne `sites.client_id` (clé étrangère vers `clients`).

    Clés étrangères suspendues : sinon supprimer l'ancienne table `sites` effacerait en cascade caméras et accès.
    SQLite ne sait pas supprimer une colonne qui porte une clé étrangère : la table `sites` est reconstruite.
    """
    tables = {r[0] for r in _conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rebuild = "client_id" in {r[1] for r in _conn.execute("PRAGMA table_info(sites)")}
    if not rebuild and not tables & set(OBSOLETE):
        return
    _conn.commit()
    _conn.execute("PRAGMA foreign_keys=OFF")
    try:
        script = "BEGIN;\n" + "".join(f"DROP TABLE IF EXISTS {t};\n" for t in OBSOLETE)
        if rebuild:
            script += """CREATE TABLE sites_new(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, notes TEXT DEFAULT '');
                         INSERT INTO sites_new(id,name,notes) SELECT id,name,notes FROM sites;
                         DROP TABLE sites;
                         ALTER TABLE sites_new RENAME TO sites;\n"""
        _conn.executescript(script + "COMMIT;")
    finally:
        _conn.execute("PRAGMA foreign_keys=ON")


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def setting(key: str, default: str = "") -> str:
    row = one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now() -> int:
    return int(time.time())
