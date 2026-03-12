"""SQLite database management."""
import sqlite3
import time
import json
from pathlib import Path
from contextlib import contextmanager
from config import DB_PATH, MANAGER_DIR, DEFAULT_PLANS, DEFAULT_SERVER_BANDWIDTH_MBPS, DEFAULT_SERVER_MONTHLY_TRAFFIC_TB

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    duration_hours INTEGER NOT NULL,
    traffic_gb REAL NOT NULL,
    bandwidth_mbps INTEGER NOT NULL DEFAULT 50,
    price REAL NOT NULL DEFAULT 0,
    max_connections INTEGER NOT NULL DEFAULT 5
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    token TEXT NOT NULL UNIQUE,
    plan_id INTEGER NOT NULL DEFAULT 0,
    remark TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    traffic_limit_bytes INTEGER NOT NULL DEFAULT 0,
    traffic_up_bytes INTEGER NOT NULL DEFAULT 0,
    traffic_down_bytes INTEGER NOT NULL DEFAULT 0,
    traffic_used_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS traffic_snapshots (
    uuid TEXT PRIMARY KEY,
    upload_bytes INTEGER NOT NULL DEFAULT 0,
    download_bytes INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    operator TEXT DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    plan_id INTEGER NOT NULL,
    plan_name TEXT NOT NULL DEFAULT '',
    price REAL NOT NULL DEFAULT 0,
    source TEXT DEFAULT 'manual',
    created_at INTEGER NOT NULL
);
"""


@contextmanager
def get_db():
    """Get a database connection with WAL mode."""
    MANAGER_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize database schema and default data."""
    with get_db() as db:
        db.executescript(SCHEMA)
        _migrate_columns(db)

        # Seed default plans
        existing = db.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        if existing == 0:
            for pid, name, hours, gb, bw, price, max_conn in DEFAULT_PLANS:
                db.execute(
                    "INSERT INTO plans (id, name, duration_hours, traffic_gb, bandwidth_mbps, price, max_connections) VALUES (?,?,?,?,?,?,?)",
                    (pid, name, hours, gb, bw, price, max_conn),
                )

        # Seed default config
        defaults = {
            "sub_port": "8888",
            "api_secret": "",
            "protocols": '["vless-reality"]',
            "server_bandwidth_mbps": str(DEFAULT_SERVER_BANDWIDTH_MBPS),
            "server_monthly_traffic_tb": str(DEFAULT_SERVER_MONTHLY_TRAFFIC_TB),
            "admin_password": "",
            "admin_port": "8880",
            "auto_purge_days": "7",
        }
        for k, v in defaults.items():
            db.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))


def _migrate_columns(conn):
    """Add columns that may not exist in older databases."""
    migrations = [
        ("plans", "price", "REAL NOT NULL DEFAULT 0"),
        ("plans", "max_connections", "INTEGER NOT NULL DEFAULT 5"),
    ]
    for table, col, col_type in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # Column already exists


def get_config(key: str, default: str = "") -> str:
    with get_db() as db:
        row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


def set_config(key: str, value: str):
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))


def get_config_json(key: str, default=None):
    val = get_config(key, "")
    if val:
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    return default


def migrate_from_json():
    """Migrate data from old JSON files to SQLite."""
    old_users = MANAGER_DIR / "users.json"
    old_plans = MANAGER_DIR / "plans.json"
    old_config = MANAGER_DIR / "config.json"

    with get_db() as db:
        # Migrate plans
        if old_plans.exists():
            try:
                data = json.loads(old_plans.read_text())
                for p in data.get("plans", []):
                    db.execute(
                        "INSERT OR REPLACE INTO plans VALUES (?,?,?,?,?,?,?)",
                        (p["id"], p["name"], p["duration_hours"], p["traffic_gb"],
                         p.get("bandwidth_mbps", 50), p.get("price", 0), p.get("max_connections", 5)),
                    )
            except Exception:
                pass

        # Migrate users
        if old_users.exists():
            try:
                data = json.loads(old_users.read_text())
                for u in data.get("users", []):
                    db.execute(
                        "INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            u["id"], u["uuid"], u["token"], u.get("plan_id", 0),
                            u.get("remark", ""), u["created_at"], u["expires_at"],
                            u.get("traffic_limit_bytes", 0), u.get("traffic_up_bytes", 0),
                            u.get("traffic_down_bytes", 0), u.get("traffic_used_bytes", 0),
                            u.get("status", "active"),
                        ),
                    )
            except Exception:
                pass

        # Migrate config
        if old_config.exists():
            try:
                data = json.loads(old_config.read_text())
                for k, v in data.items():
                    val = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
                    db.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, val))
            except Exception:
                pass

    # Rename old files
    for f in [old_users, old_plans, old_config]:
        if f.exists():
            f.rename(f.with_suffix(".json.bak"))
