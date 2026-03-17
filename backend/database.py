"""
database.py — SQLite database setup and all data access functions.
"""

import sqlite3
import os
from datetime import date, datetime, timedelta
from typing import Optional

DB_PATH = os.environ.get("DB_PATH", "tradeguardian.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id             INTEGER PRIMARY KEY REFERENCES users(id),
            account_size        REAL    NOT NULL DEFAULT 10000,
            max_risk_pct        REAL    NOT NULL DEFAULT 2.0,
            max_trades_day      INTEGER NOT NULL DEFAULT 5,
            max_loss_day        REAL    NOT NULL DEFAULT 200,
            pip_value           REAL    NOT NULL DEFAULT 10.0,
            cooldown_minutes    INTEGER NOT NULL DEFAULT 10
        );

        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            account_size    REAL    NOT NULL,
            lot_size        REAL    NOT NULL,
            stop_loss_pips  REAL    NOT NULL,
            actual_risk     REAL    NOT NULL,
            status          TEXT    NOT NULL,
            result          TEXT,
            pnl             REAL,
            followed_rules  INTEGER DEFAULT NULL,
            revenge_trading INTEGER DEFAULT NULL,
            valid_setup     INTEGER DEFAULT NULL,
            emotion_score   INTEGER DEFAULT NULL,
            traded_at       TEXT    DEFAULT (datetime('now')),
            trade_date      TEXT    DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS rule_violations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            violation_type  TEXT    NOT NULL,
            detail          TEXT,
            trade_id        INTEGER REFERENCES trades(id),
            violated_at     TEXT    DEFAULT (datetime('now')),
            violation_date  TEXT    DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS cooldowns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            reason      TEXT    NOT NULL,
            reflection  TEXT,
            started_at  TEXT    DEFAULT (datetime('now')),
            ends_at     TEXT    NOT NULL,
            completed   INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()


# ─── Users ─────────────────────────────────────────────────────────────────────

def get_or_create_user(username: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    if row:
        user_id = row["id"]
    else:
        cursor.execute("INSERT INTO users (username) VALUES (?)", (username,))
        user_id = cursor.lastrowid
        cursor.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
    conn.close()
    return user_id


def get_settings(user_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}


def update_settings(user_id: int, **kwargs):
    allowed = {"account_size", "max_risk_pct", "max_trades_day",
               "max_loss_day", "pip_value", "cooldown_minutes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    conn = get_connection()
    conn.execute(f"UPDATE user_settings SET {set_clause} WHERE user_id = ?", values)
    conn.commit()
    conn.close()


# ─── Trades ────────────────────────────────────────────────────────────────────

def save_trade(user_id: int, account_size: float, lot_size: float,
               stop_loss_pips: float, actual_risk: float, status: str,
               followed_rules=None, revenge_trading=None,
               valid_setup=None, emotion_score=None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO trades (user_id, account_size, lot_size, stop_loss_pips,
                            actual_risk, status, followed_rules, revenge_trading,
                            valid_setup, emotion_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, account_size, lot_size, stop_loss_pips, actual_risk, status,
          followed_rules, revenge_trading, valid_setup, emotion_score))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def close_trade(trade_id: int, result: str, pnl: float):
    conn = get_connection()
    conn.execute("UPDATE trades SET result = ?, pnl = ? WHERE id = ?",
                 (result.upper(), pnl, trade_id))
    conn.commit()
    conn.close()


def get_today_stats(user_id: int) -> dict:
    today = date.today().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            COUNT(*) as trade_count,
            COALESCE(SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END), 0) as total_loss,
            COALESCE(SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END), 0) as wins,
            COALESCE(SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END), 0) as losses
        FROM trades
        WHERE user_id = ? AND trade_date = ? AND status != 'REJECTED'
    """, (user_id, today))
    row = cursor.fetchone()
    conn.close()
    return dict(row)


def get_loss_streak(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT result FROM trades
        WHERE user_id = ? AND result IN ('WIN', 'LOSS')
        ORDER BY traded_at DESC LIMIT 10
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    streak = 0
    for row in rows:
        if row["result"] == "LOSS":
            streak += 1
        else:
            break
    return streak


def get_recent_trades(user_id: int, limit: int = 20) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, lot_size, stop_loss_pips, actual_risk, status, result, pnl,
               followed_rules, revenge_trading, valid_setup, emotion_score, traded_at
        FROM trades WHERE user_id = ?
        ORDER BY traded_at DESC LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Violations ────────────────────────────────────────────────────────────────

def log_violation(user_id: int, violation_type: str, detail: str,
                  trade_id: Optional[int] = None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO rule_violations (user_id, violation_type, detail, trade_id)
        VALUES (?, ?, ?, ?)
    """, (user_id, violation_type, detail, trade_id))
    conn.commit()
    conn.close()


def get_violations_summary(user_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT violation_type, COUNT(*) as count
        FROM rule_violations
        WHERE user_id = ? AND violation_date >= date('now', 'start of month')
        GROUP BY violation_type ORDER BY count DESC
    """, (user_id,))
    by_type = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT COUNT(*) as total FROM rule_violations
        WHERE user_id = ? AND violation_date >= date('now', 'start of month')
    """, (user_id,))
    total = cursor.fetchone()["total"]

    cursor.execute("""
        SELECT COALESCE(SUM(ABS(t.pnl)), 0) as violation_cost
        FROM rule_violations rv JOIN trades t ON rv.trade_id = t.id
        WHERE rv.user_id = ? AND rv.violation_date >= date('now', 'start of month')
          AND t.result = 'LOSS' AND t.pnl IS NOT NULL
    """, (user_id,))
    cost = cursor.fetchone()["violation_cost"]

    cursor.execute("""
        SELECT COUNT(*) as today FROM rule_violations
        WHERE user_id = ? AND violation_date = date('now')
    """, (user_id,))
    today = cursor.fetchone()["today"]

    cursor.execute("""
        SELECT violation_type, detail, violated_at FROM rule_violations
        WHERE user_id = ? ORDER BY violated_at DESC LIMIT 8
    """, (user_id,))
    recent = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {
        "total_this_month": total,
        "today": today,
        "by_type": by_type,
        "violation_cost": round(cost, 2),
        "recent": recent
    }


# ─── Cooldown ──────────────────────────────────────────────────────────────────

def start_cooldown(user_id: int, reason: str, minutes: int) -> dict:
    now = datetime.utcnow()
    ends = now + timedelta(minutes=minutes)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO cooldowns (user_id, reason, ends_at) VALUES (?, ?, ?)
    """, (user_id, reason, ends.isoformat()))
    cooldown_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"id": cooldown_id, "reason": reason,
            "ends_at": ends.isoformat(), "minutes": minutes}


def get_active_cooldown(user_id: int) -> Optional[dict]:
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, reason, ends_at, started_at FROM cooldowns
        WHERE user_id = ? AND completed = 0 AND ends_at > ?
        ORDER BY ends_at DESC LIMIT 1
    """, (user_id, now))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def complete_cooldown(cooldown_id: int, reflection: str):
    conn = get_connection()
    conn.execute("UPDATE cooldowns SET completed = 1, reflection = ? WHERE id = ?",
                 (reflection, cooldown_id))
    conn.commit()
    conn.close()


# ─── Checklist insights ────────────────────────────────────────────────────────

def get_checklist_insights(user_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor()

    def win_rate_for(field: str, value: int):
        cursor.execute(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins
            FROM trades
            WHERE user_id = ? AND {field} = ? AND result IN ('WIN','LOSS')
        """, (user_id, value))
        row = cursor.fetchone()
        if row and row["total"] >= 3:
            return round((row["wins"] / row["total"]) * 100, 1), row["total"]
        return None, 0

    insights = []
    for field, label, flip in [
        ("followed_rules", "Following your rules", False),
        ("revenge_trading", "Avoiding revenge trades", True),
        ("valid_setup",     "Trading valid setups", False),
    ]:
        yes_wr, yes_n = win_rate_for(field, 1)
        no_wr,  no_n  = win_rate_for(field, 0)
        if yes_wr is not None or no_wr is not None:
            insights.append({
                "label": label,
                "yes_wr": yes_wr, "yes_n": yes_n,
                "no_wr": no_wr,   "no_n": no_n,
                "flip": flip
            })

    cursor.execute("""
        SELECT emotion_score,
               COUNT(*) as total,
               SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE user_id = ? AND emotion_score IS NOT NULL AND result IN ('WIN','LOSS')
        GROUP BY emotion_score ORDER BY emotion_score
    """, (user_id,))
    emotion_data = []
    for r in cursor.fetchall():
        if r["total"] >= 2:
            emotion_data.append({
                "score": r["emotion_score"],
                "win_rate": round((r["wins"] / r["total"]) * 100, 1),
                "total": r["total"]
            })

    conn.close()
    return {"comparisons": insights, "emotion": emotion_data}
