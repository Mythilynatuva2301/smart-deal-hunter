"""SQLite storage shared by the Streamlit app and the background worker."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

DB_PATH = os.getenv("DEAL_DB", "deals.db")


def now() -> str:
    """UTC timestamp as an ISO string (all times are stored in UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def in_minutes(minutes: float) -> str:
    t = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=minutes)
    return t.isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")  # lets the app read while the worker writes
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'mock',      -- mock | http | browser
                url TEXT,
                selector TEXT,                           -- CSS selector of the price element
                target_price REAL,
                min_interval INTEGER NOT NULL DEFAULT 12, -- minutes
                max_interval INTEGER NOT NULL DEFAULT 30, -- minutes
                next_check_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                price REAL NOT NULL,
                checked_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_prices_product ON prices(product_id, checked_at);
            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                verdict TEXT NOT NULL,                   -- BUY NOW | WAIT
                reason TEXT,
                source TEXT,                             -- llm | rules
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


# ---------- products ----------
def add_product(name, mode, url, selector, target_price, min_interval=12, max_interval=30):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO products(name, mode, url, selector, target_price, min_interval, "
            "max_interval, next_check_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, mode, url, selector, target_price, min_interval, max_interval, now(), now()),
        )
        return cur.lastrowid


def list_products():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM products ORDER BY id DESC")]


def delete_product(product_id):
    with conn() as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("DELETE FROM products WHERE id=?", (product_id,))


def due_products():
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM products WHERE next_check_at IS NULL OR next_check_at <= ?", (now(),)
        )
        return [dict(r) for r in rows]


def set_next_check(product_id, minutes):
    with conn() as c:
        c.execute("UPDATE products SET next_check_at=? WHERE id=?", (in_minutes(minutes), product_id))


def set_error(product_id, error):
    with conn() as c:
        c.execute("UPDATE products SET last_error=? WHERE id=?", (error, product_id))


# ---------- prices ----------
def add_price(product_id, price):
    with conn() as c:
        c.execute(
            "INSERT INTO prices(product_id, price, checked_at) VALUES (?,?,?)",
            (product_id, price, now()),
        )


def get_prices(product_id, limit=500):
    """Most recent `limit` prices, oldest first."""
    with conn() as c:
        rows = c.execute(
            "SELECT price, checked_at FROM prices WHERE product_id=? "
            "ORDER BY id DESC LIMIT ?",
            (product_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---------- recommendations & alerts ----------
def add_recommendation(product_id, verdict, reason, source):
    with conn() as c:
        c.execute(
            "INSERT INTO recommendations(product_id, verdict, reason, source, created_at) "
            "VALUES (?,?,?,?,?)",
            (product_id, verdict, reason, source, now()),
        )


def latest_recommendation(product_id):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM recommendations WHERE product_id=? ORDER BY id DESC LIMIT 1",
            (product_id,),
        ).fetchone()
    return dict(r) if r else None


def add_alert(product_id, message):
    with conn() as c:
        c.execute(
            "INSERT INTO alerts(product_id, message, created_at) VALUES (?,?,?)",
            (product_id, message, now()),
        )


def recent_alerts(limit=15):
    with conn() as c:
        rows = c.execute(
            "SELECT a.message, a.created_at, p.name FROM alerts a "
            "JOIN products p ON p.id=a.product_id ORDER BY a.id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]
