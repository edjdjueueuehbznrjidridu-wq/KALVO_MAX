# -*- coding: utf-8 -*-
"""KALVO paper-trading ledger and SQLite persistence.

All funds and orders in this module are simulated. No exchange credentials or
live-order endpoints are used here.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

FEE_RATE = 0.0006
MAX_OPEN_POSITIONS = 20
MAX_PENDING_ORDERS = 50
MAX_OPEN_ALERTS = 30
MAX_LEVERAGE = 5.0
MAX_TRAIL_PCT = 20.0
_DB_LOCK = threading.RLock()
# Fingerprint of the credential accidentally bundled in earlier KALVO builds.
# The credential itself is deliberately not retained in source.
_LEGACY_BUNDLED_KEY_SHA256 = "23046542b04b6c7107efa0a8cf8d4aad95464bc5e0b85ef1fdd7a3c54260af4e"


def _db_path() -> str:
    base = os.environ.get("APEX_DATA") or os.path.join(os.path.expanduser("~"), ".apex_native")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "apex.db")


def configure_data_dir(path: str) -> str:
    """Use the private Android app directory and migrate the previous local DB once."""
    old_db = _db_path()
    path = os.path.abspath(os.path.expanduser(str(path)))
    os.makedirs(path, exist_ok=True)
    new_db = os.path.join(path, "apex.db")
    if os.path.abspath(old_db) != os.path.abspath(new_db) and os.path.isfile(old_db) and not os.path.exists(new_db):
        source = target = None
        try:
            source = sqlite3.connect(old_db, timeout=15)
            target = sqlite3.connect(new_db, timeout=15)
            source.backup(target)
            target.commit()
            # Scrub obsolete plaintext provider credentials from the old copy too.
            try:
                source.execute("DELETE FROM settings WHERE key IN ('coarena_key','coarena_api_key')")
                source.commit()
                source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.DatabaseError:
                pass
        except Exception:
            try:
                if target:
                    target.close()
            except Exception:
                pass
            try:
                if os.path.exists(new_db):
                    os.remove(new_db)
            except Exception:
                pass
        finally:
            if source:
                source.close()
            if target:
                target.close()
    os.environ["APEX_DATA"] = path
    return path


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=20, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=20000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _columns(c: sqlite3.Connection, table: str) -> set:
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(c: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    if name not in _columns(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    """Create tables and migrate older app databases without deleting user data."""
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS wallet(
              id INTEGER PRIMARY KEY CHECK(id=1), balance REAL NOT NULL,
              equity REAL NOT NULL, realized_pnl REAL NOT NULL,
              total_deposited REAL NOT NULL, peak_equity REAL NOT NULL,
              fees_paid REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS positions(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              symbol TEXT NOT NULL, side TEXT NOT NULL, entry REAL NOT NULL,
              qty REAL NOT NULL, qty_open REAL NOT NULL, leverage REAL NOT NULL,
              margin REAL NOT NULL, margin_open REAL NOT NULL,
              tp1 REAL, tp2 REAL, tp3 REAL, sl REAL,
              trail_pct REAL DEFAULT 0, trail_active INTEGER DEFAULT 0,
              status TEXT NOT NULL, opened_ts INTEGER NOT NULL,
              note TEXT DEFAULT '', source TEXT DEFAULT 'manual'
            );
            CREATE TABLE IF NOT EXISTS trades(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              pos_id INTEGER, symbol TEXT, side TEXT, entry REAL, exit REAL,
              qty REAL, leverage REAL, pnl REAL, pnl_pct REAL, fee REAL,
              reason TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS notes(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT, body TEXT, symbol TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS ledger(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              type TEXT, amount REAL, balance_after REAL, note TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS chat_log(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              role TEXT, content TEXT, model TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS orders(
              id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
              side TEXT NOT NULL, order_type TEXT NOT NULL, trigger_price REAL NOT NULL,
              margin REAL NOT NULL, leverage REAL NOT NULL, tp1 REAL, tp2 REAL,
              tp3 REAL, sl REAL, trail_pct REAL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending', created_ts INTEGER NOT NULL,
              note TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS watchlist(
              symbol TEXT PRIMARY KEY, added_ts INTEGER NOT NULL, note TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS alerts(
              id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
              condition TEXT NOT NULL CHECK(condition IN ('above','below')),
              price REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open',
              created_ts INTEGER NOT NULL, triggered_ts INTEGER, note TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS spot_balances(
              asset TEXT PRIMARY KEY, free REAL NOT NULL DEFAULT 0,
              avg_cost REAL NOT NULL DEFAULT 0, updated_ts INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS spot_trades(
              id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
              side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
              quantity REAL NOT NULL, price REAL NOT NULL, quote_amount REAL NOT NULL,
              fee REAL NOT NULL DEFAULT 0, realized_pnl REAL NOT NULL DEFAULT 0,
              ts INTEGER NOT NULL, note TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS spot_ledger(
              id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT NOT NULL,
              type TEXT NOT NULL, amount REAL NOT NULL, balance_after REAL NOT NULL,
              note TEXT DEFAULT '', ts INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notifications(
              id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL,
              title TEXT NOT NULL, body TEXT NOT NULL, created_ts INTEGER NOT NULL,
              read_ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS smart_alerts(
              id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
              metric TEXT NOT NULL CHECK(metric IN ('change_pct')),
              condition TEXT NOT NULL CHECK(condition IN ('above','below')),
              threshold REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open',
              created_ts INTEGER NOT NULL, triggered_ts INTEGER, note TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_smart_alerts_status ON smart_alerts(status);
            """)
            # Safe additive migrations for databases created by earlier app builds.
            for name, ddl in (
                ("entry_fee_remaining", "REAL DEFAULT 0"),
                ("tp1_hit", "INTEGER DEFAULT 0"),
                ("tp2_hit", "INTEGER DEFAULT 0"),
                ("tp3_hit", "INTEGER DEFAULT 0"),
                ("best_price", "REAL"),
                ("trail_stop", "REAL"),
            ):
                _add_column(c, "positions", name, ddl)
            _add_column(c, "trades", "entry_fee", "REAL DEFAULT 0")
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT id FROM wallet WHERE id=1").fetchone():
                c.execute("INSERT INTO wallet VALUES(1,100,100,0,100,100,0)")
                c.execute(
                    "INSERT INTO ledger(type,amount,balance_after,note,ts) VALUES(?,?,?,?,?)",
                    ("demo_seed", 100.0, 100.0, "موجودی اولیه حساب آزمایشی", int(time.time())),
                )
            # Separate virtual spot wallet; never shares collateral with futures demo.
            if not c.execute("SELECT 1 FROM spot_balances WHERE asset='USDT'").fetchone():
                now = int(time.time())
                c.execute("INSERT INTO spot_balances(asset,free,avg_cost,updated_ts) VALUES('USDT',1000,1,?)", (now,))
                c.execute("INSERT INTO spot_ledger(asset,type,amount,balance_after,note,ts) VALUES(?,?,?,?,?,?)",
                          ("USDT", "demo_seed", 1000.0, 1000.0, "موجودی اولیهٔ مجازی اسپات", now))
            # Repair impossible values that older demo builds could have saved.
            wallet = _wallet_row(c)
            raw_balance = wallet["balance"]
            try:
                balance = float(raw_balance)
            except (TypeError, ValueError):
                balance = float("nan")
            if not math.isfinite(balance) or balance < 0:
                correction = -balance if math.isfinite(balance) else 0.0
                c.execute("UPDATE wallet SET balance=0,equity=0 WHERE id=1")
                _ledger(c, "migration_repair", correction, 0.0, "اصلاح موجودی نامعتبر نسخهٔ قبلی")
                balance = 0.0
            peak = wallet["peak_equity"]
            try:
                peak = float(peak)
            except (TypeError, ValueError):
                peak = 0.0
            if not math.isfinite(peak) or peak < balance:
                peak = balance
            realized = wallet["realized_pnl"]
            try:
                realized = float(realized)
            except (TypeError, ValueError):
                realized = 0.0
            if not math.isfinite(realized):
                realized = 0.0
            total_deposited = wallet["total_deposited"]
            fees_paid = wallet["fees_paid"]
            try:
                total_deposited = float(total_deposited)
            except (TypeError, ValueError):
                total_deposited = 0.0
            try:
                fees_paid = float(fees_paid)
            except (TypeError, ValueError):
                fees_paid = 0.0
            if not math.isfinite(total_deposited) or total_deposited < 0:
                total_deposited = 0.0
            if not math.isfinite(fees_paid) or fees_paid < 0:
                fees_paid = 0.0
            c.execute(
                "UPDATE wallet SET balance=?,equity=?,realized_pnl=?,total_deposited=?,peak_equity=?,fees_paid=? WHERE id=1",
                (balance, balance, realized, total_deposited, peak, fees_paid),
            )
            # Legacy builds may have shipped an embedded/plaintext API key. Scrub it
            # exactly once; afterwards a key the user typed themselves is kept.
            done = c.execute("SELECT value FROM settings WHERE key='legacy_key_scrubbed'").fetchone()
            if not done:
                c.execute("DELETE FROM settings WHERE key IN ('coarena_key','coarena_api_key')")
                c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('legacy_key_scrubbed','1')")
            # Some 6.0/6.1 installs re-seeded that bundled key after the first
            # legacy cleanup. Remove only that exact value, not user-entered keys.
            scrub_v2 = c.execute(
                "SELECT value FROM settings WHERE key='legacy_bundled_key_scrubbed_v2'"
            ).fetchone()
            if not scrub_v2:
                for key_name in ("coarena_key", "coarena_api_key"):
                    row = c.execute("SELECT value FROM settings WHERE key=?", (key_name,)).fetchone()
                    value = str(row[0] or "") if row else ""
                    if value and hashlib.sha256(value.encode("utf-8")).hexdigest() == _LEGACY_BUNDLED_KEY_SHA256:
                        c.execute("DELETE FROM settings WHERE key=?", (key_name,))
                c.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES('legacy_bundled_key_scrubbed_v2','1')"
                )
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def _number(value: Any, name: str, *, minimum: Optional[float] = None,
            maximum: Optional[float] = None, allow_zero: bool = True) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} معتبر نیست")
    if not math.isfinite(x):
        raise ValueError(f"{name} باید عدد محدود باشد")
    if not allow_zero and x <= 0:
        raise ValueError(f"{name} باید بزرگ‌تر از صفر باشد")
    if minimum is not None and x < minimum:
        raise ValueError(f"{name} کمتر از حد مجاز است")
    if maximum is not None and x > maximum:
        raise ValueError(f"{name} بیشتر از حد مجاز است")
    return x


def _symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip().replace("_", "-").replace("/", "-")
    s = s.replace(" ", "")
    if s.endswith("USDT") and not s.endswith("-USDT"):
        s = s[:-4] + "-USDT"
    if not s.endswith("-USDT"):
        s += "-USDT"
    if not re.fullmatch(r"[A-Z0-9]{2,20}-USDT", s):
        raise ValueError("نماد نامعتبر است؛ نمونه: BTC یا BTC-USDT")
    return s


def _check_levels(side: str, entry: float, tp1, tp2, tp3, sl) -> tuple:
    vals = []
    for name, value in (("TP1", tp1), ("TP2", tp2), ("TP3", tp3), ("SL", sl)):
        vals.append(None if value in (None, "") else _number(value, name, minimum=0, allow_zero=False))
    a, b, d, stop = vals
    targets = [x for x in (a, b, d) if x is not None]
    if len(targets) != sum(x is not None for x in vals[:3]):
        raise ValueError("سطوح حد سود نامعتبر است")
    if side == "LONG":
        if stop is not None and stop >= entry:
            raise ValueError("حد ضرر لانگ باید پایین‌تر از ورود باشد")
        if any(x <= entry for x in targets):
            raise ValueError("حد سود لانگ باید بالاتر از ورود باشد")
        if any(left >= right for left, right in zip(targets, targets[1:])):
            raise ValueError("ترتیب هدف‌های لانگ باید صعودی و بدون تکرار باشد")
    else:
        if stop is not None and stop <= entry:
            raise ValueError("حد ضرر شورت باید بالاتر از ورود باشد")
        if any(x >= entry for x in targets):
            raise ValueError("حد سود شورت باید پایین‌تر از ورود باشد")
        if any(left <= right for left, right in zip(targets, targets[1:])):
            raise ValueError("ترتیب هدف‌های شورت باید نزولی و بدون تکرار باشد")
    if b is not None and a is None or d is not None and b is None:
        raise ValueError("برای TP2 ابتدا TP1 و برای TP3 ابتدا TP2 را تعیین کنید")
    return a, b, d, stop


def _wallet_row(c: sqlite3.Connection) -> sqlite3.Row:
    row = c.execute("SELECT * FROM wallet WHERE id=1").fetchone()
    if row is None:
        raise RuntimeError("حساب دمو راه‌اندازی نشده است؛ برنامه را دوباره باز کنید")
    return row


def _wallet_update(c: sqlite3.Connection, balance: float, *, realized_delta: float = 0.0,
                   fees_add: float = 0.0, deposit_add: float = 0.0) -> None:
    w = _wallet_row(c)
    balance = max(0.0, float(balance))
    peak = max(float(w["peak_equity"] or 0), balance)
    c.execute(
        """UPDATE wallet SET balance=?, equity=?, realized_pnl=?, fees_paid=?,
           total_deposited=?, peak_equity=? WHERE id=1""",
        (balance, balance, float(w["realized_pnl"] or 0) + realized_delta,
         float(w["fees_paid"] or 0) + fees_add,
         float(w["total_deposited"] or 0) + deposit_add, peak),
    )


def _ledger(c, typ: str, amount: float, balance: float, note: str = "") -> None:
    c.execute(
        "INSERT INTO ledger(type,amount,balance_after,note,ts) VALUES(?,?,?,?,?)",
        (typ, float(amount), float(balance), str(note or "")[:160], int(time.time())),
    )


def get_setting(key: str, default: str = "") -> str:
    c = conn()
    try:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    finally:
        c.close()


def set_setting(key: str, value: str) -> None:
    c = conn()
    try:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))
        c.commit()
    finally:
        c.close()


def delete_setting(key: str) -> None:
    c = conn()
    try:
        c.execute("DELETE FROM settings WHERE key=?", (key,))
        c.commit()
    finally:
        c.close()


def get_wallet() -> Dict[str, float]:
    c = conn()
    try:
        r = _wallet_row(c)
        return dict(r)
    finally:
        c.close()


def deposit(amount: float, note: str = "") -> Dict[str, float]:
    amount = _number(amount, "مبلغ واریز", minimum=0, allow_zero=False)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            w = _wallet_row(c)
            bal = float(w["balance"]) + amount
            _wallet_update(c, bal, deposit_add=amount)
            _ledger(c, "deposit", amount, bal, note)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    return get_wallet()


def _pending_reserved(c: sqlite3.Connection) -> float:
    return float(c.execute(
        "SELECT COALESCE(SUM(margin + margin*leverage*?),0) FROM orders WHERE status='pending'",
        (FEE_RATE,),
    ).fetchone()[0])


def withdraw(amount: float, note: str = "") -> Dict[str, float]:
    amount = _number(amount, "مبلغ برداشت", minimum=0, allow_zero=False)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            w = _wallet_row(c)
            bal = float(w["balance"])
            reserved = _pending_reserved(c)
            available = max(0.0, bal - reserved)
            if amount > available + 1e-9:
                raise ValueError(
                    f"موجودی قابل برداشت کافی نیست؛ {reserved:.2f} برای سفارش‌های در انتظار رزرو شده، "
                    f"موجودی قابل برداشت {available:.2f} است"
                )
            bal -= amount
            _wallet_update(c, bal)
            _ledger(c, "withdraw", -amount, bal, note)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    return get_wallet()


def _open_tx(c: sqlite3.Connection, symbol: str, side: str, entry: float,
             leverage: float, margin: float, tp1=None, tp2=None, tp3=None,
             sl=None, note: str = "", source: str = "manual", trail_pct: float = 0.0,
             filling_order_id: Optional[int] = None) -> Dict[str, Any]:
    side = (side or "").upper().strip()
    if side not in ("LONG", "SHORT"):
        raise ValueError("سمت باید LONG یا SHORT باشد")
    symbol = _symbol(symbol)
    entry = _number(entry, "قیمت ورود", minimum=0, allow_zero=False)
    leverage = _number(leverage, "اهرم", minimum=1, maximum=MAX_LEVERAGE, allow_zero=False)
    margin = _number(margin, "مارجین", minimum=0, allow_zero=False)
    trail_pct = _number(trail_pct or 0, "تریل", minimum=0, maximum=MAX_TRAIL_PCT)
    tp1, tp2, tp3, sl = _check_levels(side, entry, tp1, tp2, tp3, sl)
    count = int(c.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0])
    if count >= MAX_OPEN_POSITIONS:
        raise ValueError(f"حداکثر {MAX_OPEN_POSITIONS} پوزیشن باز مجاز است")
    w = _wallet_row(c)
    balance = float(w["balance"])
    notional = margin * leverage
    qty = notional / entry
    entry_fee = notional * FEE_RATE
    required = margin + entry_fee
    reserve_sql = "SELECT COALESCE(SUM(margin + margin*leverage*?),0) FROM orders WHERE status='pending'"
    reserve_args = [FEE_RATE]
    if filling_order_id is not None:
        reserve_sql += " AND id<>?"
        reserve_args.append(int(filling_order_id))
    pending_reserved = float(c.execute(reserve_sql, reserve_args).fetchone()[0])
    if required + pending_reserved > balance + 1e-9:
        raise ValueError(f"موجودی آزاد کافی نیست؛ نیاز این پوزیشن {required:.2f} + رزرو سفارش‌ها {pending_reserved:.2f} / موجود {balance:.2f}")
    new_balance = balance - required
    _wallet_update(c, new_balance, realized_delta=-entry_fee, fees_add=entry_fee)
    now = int(time.time())
    cur = c.execute(
        """INSERT INTO positions(
          symbol,side,entry,qty,qty_open,leverage,margin,margin_open,
          tp1,tp2,tp3,sl,trail_pct,trail_active,status,opened_ts,note,source,
          entry_fee_remaining,tp1_hit,tp2_hit,tp3_hit,best_price,trail_stop)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,'open',?,?,?,?,0,0,0,?,NULL)""",
        (symbol, side, entry, qty, qty, leverage, margin, margin,
         tp1, tp2, tp3, sl, trail_pct, now, str(note or "")[:200],
         str(source or "manual")[:30], entry_fee, entry),
    )
    pid = int(cur.lastrowid)
    _ledger(c, "margin_lock", -margin, new_balance, f"قفل مارجین پوزیشن #{pid}")
    if entry_fee:
        _ledger(c, "entry_fee", -entry_fee, new_balance, f"کارمزد ورود پوزیشن #{pid}")
    return {"id": pid, "symbol": symbol, "side": side, "entry": entry,
            "qty": qty, "margin": margin, "leverage": leverage,
            "notional": notional, "entry_fee": entry_fee}


def open_position(symbol: str, side: str, entry: float, leverage: float, margin: float,
                  tp1=None, tp2=None, tp3=None, sl=None, note: str = "",
                  source: str = "manual", trail_pct: float = 0.0) -> Dict[str, Any]:
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            result = _open_tx(c, symbol, side, entry, leverage, margin, tp1, tp2,
                              tp3, sl, note, source, trail_pct)
            c.commit()
            return result
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def list_open_positions() -> List[Dict[str, Any]]:
    c = conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM positions WHERE status='open' ORDER BY id DESC").fetchall()]
    finally:
        c.close()


def position_pnl(pos: Dict[str, Any], price: float) -> Dict[str, float]:
    """Return net close-now PnL plus mark-to-market PnL for equity.

    Entry fees have already reduced free cash, so ``mtm_pnl`` subtracts only
    the estimated exit fee. ``pnl`` additionally allocates the unpaid portion
    of the entry fee for a net position display.
    """
    price = _number(price, "قیمت بازار", minimum=0, allow_zero=False)
    entry = float(pos["entry"])
    qty = float(pos["qty_open"])
    gross = (price - entry) * qty if pos["side"] == "LONG" else (entry - price) * qty
    exit_fee = abs(price * qty) * FEE_RATE
    entry_fee_remaining = max(0.0, float(pos.get("entry_fee_remaining") or 0.0))
    mtm_pnl = gross - exit_fee
    net_close_pnl = mtm_pnl - entry_fee_remaining
    notional = entry * qty
    margin = float(pos.get("margin_open") or pos.get("margin") or 0)
    return {"pnl": net_close_pnl, "gross_pnl": gross, "mtm_pnl": mtm_pnl,
            "estimated_exit_fee": exit_fee, "entry_fee_remaining": entry_fee_remaining,
            "pnl_pct": (net_close_pnl / notional * 100) if notional else 0.0,
            "roe": (net_close_pnl / margin * 100) if margin else 0.0, "notional": notional}


def close_position(pid: int, price: float, qty_pct: float = 100.0,
                   reason: str = "MANUAL") -> Dict[str, Any]:
    price = _number(price, "قیمت خروج", minimum=0, allow_zero=False)
    qty_pct = _number(qty_pct, "درصد بستن", minimum=0, maximum=100, allow_zero=False)
    reason = str(reason or "MANUAL").upper()[:30]
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM positions WHERE id=? AND status='open'", (int(pid),)).fetchone()
            if not row:
                raise ValueError("پوزیشن باز پیدا نشد؛ احتمالاً قبلاً بسته شده است")
            p = dict(row)
            qty_open = float(p["qty_open"])
            close_qty = qty_open * qty_pct / 100.0
            if close_qty <= 0 or close_qty > qty_open + 1e-12:
                raise ValueError("مقدار بستن نامعتبر است")
            entry = float(p["entry"])
            gross = ((price - entry) if p["side"] == "LONG" else (entry - price)) * close_qty
            exit_fee = abs(price * close_qty) * FEE_RATE
            margin_open = float(p["margin_open"] if p["margin_open"] is not None else p["margin"])
            margin_back = margin_open * close_qty / qty_open
            # Isolated-demo safety: one position cannot create negative account cash.
            net_cash_pnl = max(gross - exit_fee, -margin_back)
            old_balance = float(_wallet_row(c)["balance"])
            new_balance = max(0.0, old_balance + margin_back + net_cash_pnl)
            entry_fee_rem = float(p.get("entry_fee_remaining") or 0)
            entry_fee_alloc = entry_fee_rem * close_qty / qty_open
            new_entry_fee_rem = max(0.0, entry_fee_rem - entry_fee_alloc)
            _wallet_update(c, new_balance, realized_delta=net_cash_pnl, fees_add=exit_fee)
            new_qty = max(0.0, qty_open - close_qty)
            new_margin = max(0.0, margin_open - margin_back)
            new_status = "closed" if new_qty <= 1e-12 else "open"
            updates = {"TP1": "tp1_hit", "TP2": "tp2_hit", "TP3": "tp3_hit"}
            flag = updates.get(reason)
            if new_status == "closed":
                c.execute("""UPDATE positions SET qty_open=0,margin_open=0,status='closed',
                             entry_fee_remaining=0 WHERE id=?""", (pid,))
            else:
                if flag:
                    c.execute(f"UPDATE positions SET qty_open=?,margin_open=?,entry_fee_remaining=?,{flag}=1 WHERE id=?",
                              (new_qty, new_margin, new_entry_fee_rem, pid))
                else:
                    c.execute("UPDATE positions SET qty_open=?,margin_open=?,entry_fee_remaining=? WHERE id=?",
                              (new_qty, new_margin, new_entry_fee_rem, pid))
            # Entry fee was recognized when opening; include its allocated portion in this trade's display only.
            trade_net = net_cash_pnl - entry_fee_alloc
            base_notional = entry * close_qty
            trade_pct = (trade_net / base_notional * 100) if base_notional else 0.0
            c.execute(
                """INSERT INTO trades(pos_id,symbol,side,entry,exit,qty,leverage,pnl,pnl_pct,
                   fee,reason,ts,entry_fee) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, p["symbol"], p["side"], entry, price, close_qty, p["leverage"],
                 trade_net, trade_pct, exit_fee, reason, int(time.time()), entry_fee_alloc),
            )
            _ledger(c, "margin_release", margin_back, new_balance, f"آزادسازی مارجین پوزیشن #{pid}")
            _ledger(c, "trade_pnl", net_cash_pnl, new_balance, f"نتیجه خروج پوزیشن #{pid} ({reason})")
            if exit_fee:
                _ledger(c, "exit_fee", -exit_fee, new_balance, f"کارمزد خروج پوزیشن #{pid}")
            _notification_tx(c, "trade", "ثبت خروج دمو",
                             f"{p['symbol']} · {p['side']} · سود/زیان خالص {trade_net:+.4f} USDT · علت {reason}")
            c.commit()
            try:
                _after_trade_closed()
            except Exception:
                pass
            return {"pnl": trade_net, "gross_pnl": gross, "pnl_pct": trade_pct,
                    "fee": exit_fee + entry_fee_alloc, "qty": close_qty,
                    "remaining_qty": new_qty, "balance": new_balance}
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def create_order(symbol: str, side: str, order_type: str, trigger_price: float,
                 leverage: float, margin: float, tp1=None, tp2=None, tp3=None,
                 sl=None, trail_pct: float = 0.0, note: str = "") -> Dict[str, Any]:
    """Place a simulated market order or a pending limit/stop order."""
    side = (side or "").upper()
    order_type = (order_type or "MARKET").upper()
    if order_type not in ("MARKET", "LIMIT", "STOP"):
        raise ValueError("نوع سفارش نامعتبر است")
    symbol = _symbol(symbol)
    trigger_price = _number(trigger_price, "قیمت سفارش", minimum=0, allow_zero=False)
    if order_type == "MARKET":
        return open_position(symbol, side, trigger_price, leverage, margin, tp1, tp2, tp3,
                             sl, note, "manual", trail_pct)
    if side not in ("LONG", "SHORT"):
        raise ValueError("سمت سفارش نامعتبر است")
    # Reuse all trade-level validation before saving a pending instruction.
    fake_entry = trigger_price
    lev = _number(leverage, "اهرم", minimum=1, maximum=MAX_LEVERAGE, allow_zero=False)
    mar = _number(margin, "مارجین", minimum=0, allow_zero=False)
    trail = _number(trail_pct or 0, "تریل", minimum=0, maximum=MAX_TRAIL_PCT)
    a, b, d, stop = _check_levels(side, fake_entry, tp1, tp2, tp3, sl)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            bal = float(_wallet_row(c)["balance"])
            pending_count = int(c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0])
            if pending_count >= MAX_PENDING_ORDERS:
                raise ValueError(f"حداکثر {MAX_PENDING_ORDERS} سفارش در انتظار مجاز است")
            est_req = mar + mar * lev * FEE_RATE
            pending_reserved = float(c.execute(
                "SELECT COALESCE(SUM(margin + margin*leverage*?),0) FROM orders WHERE status='pending'", (FEE_RATE,)
            ).fetchone()[0])
            if est_req + pending_reserved > bal + 1e-9:
                raise ValueError("موجودی آزاد برای سفارش‌های در انتظار کافی نیست")
            cur = c.execute(
                """INSERT INTO orders(symbol,side,order_type,trigger_price,margin,leverage,
                   tp1,tp2,tp3,sl,trail_pct,status,created_ts,note)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                (symbol, side, order_type, trigger_price, mar, lev, a, b, d, stop,
                 trail, int(time.time()), str(note or "")[:160]),
            )
            c.commit()
            return {"id": int(cur.lastrowid), "symbol": symbol, "side": side,
                    "order_type": order_type, "trigger_price": trigger_price, "status": "pending"}
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def list_pending_orders() -> List[Dict[str, Any]]:
    c = conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM orders WHERE status='pending' ORDER BY id DESC").fetchall()]
    finally:
        c.close()


def cancel_order(order_id: int) -> bool:
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            cur = c.execute("UPDATE orders SET status='cancelled' WHERE id=? AND status='pending'", (int(order_id),))
            c.commit()
            return cur.rowcount == 1
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def auto_cooldown_active(symbol: str, seconds: int = 14400) -> bool:
    """Avoid immediately reopening the same paper symbol after an auto entry."""
    normalized = _symbol(symbol)
    c = conn()
    try:
        row = c.execute(
            "SELECT MAX(opened_ts) AS latest FROM positions WHERE source='auto' AND symbol=?",
            (normalized,),
        ).fetchone()
        latest = int(row["latest"] or 0) if row else 0
        return latest > 0 and int(time.time()) - latest < max(0, int(seconds))
    finally:
        c.close()


def process_pending_orders(price_map: Dict[str, float]) -> List[str]:
    events = []
    for order in list_pending_orders():
        sym = order["symbol"]
        base = sym[:-5] if sym.endswith("-USDT") else sym
        # New pending entries are stricter than emergency position exits.
        spread_raw = price_map.get(f"{sym}|SPREAD_PCT", price_map.get(f"{base}|SPREAD_PCT"))
        if spread_raw is not None:
            try:
                if not math.isfinite(float(spread_raw)) or float(spread_raw) > 2.0:
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
        px = price_map.get(sym) or price_map.get(base)
        if px is None:
            continue
        try:
            px = _number(px, "قیمت بازار", minimum=0, allow_zero=False)
        except ValueError:
            continue
        trigger = float(order["trigger_price"])
        if order["order_type"] == "LIMIT":
            hit = (order["side"] == "LONG" and px <= trigger) or (order["side"] == "SHORT" and px >= trigger)
        else:
            hit = (order["side"] == "LONG" and px >= trigger) or (order["side"] == "SHORT" and px <= trigger)
        if not hit:
            continue
        # If the supervisor supplied an explicit missing BBO sentinel, do not
        # manufacture a fill at last-trade price.
        action = "BUY" if order["side"] == "LONG" else "SELL"
        fill = _price_for_action(price_map, sym, action)
        if fill is None:
            # Backwards-compatible direct callers may provide only a mark. The
            # app's supervisor always adds an action key (including None) per quote.
            action_keys = (f"{sym}|{action}", f"{base}|{action}")
            if any(key in price_map for key in action_keys):
                continue
            fill = px
        if order["order_type"] == "LIMIT":
            if action == "BUY" and fill > trigger:
                continue
            if action == "SELL" and fill < trigger:
                continue
            fill = min(fill, trigger) if action == "BUY" else max(fill, trigger)
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute("SELECT * FROM orders WHERE id=? AND status='pending'", (order["id"],)).fetchone()
            if not current:
                c.rollback()
                continue
            result = _open_tx(c, current["symbol"], current["side"], fill, current["leverage"],
                              current["margin"], current["tp1"], current["tp2"], current["tp3"],
                              current["sl"], current["note"], "pending", current["trail_pct"],
                              filling_order_id=int(order["id"]))
            c.execute("UPDATE orders SET status='filled' WHERE id=?", (order["id"],))
            message = f"سفارش #{order['id']} اجرا شد؛ پوزیشن #{result['id']}"
            _notification_tx(c, "order", "اجرای سفارش دمو", message)
            c.commit()
            events.append(message)
        except Exception as e:
            c.rollback()
            # A failed fill is returned to the queue only if it was still pending.
            events.append(f"سفارش #{order['id']} اجرا نشد: {str(e)[:70]}")
        finally:
            c.close()
    return events


def _price_for(price_map: Dict[str, float], symbol: str) -> Optional[float]:
    base = symbol[:-5] if symbol.endswith("-USDT") else symbol.replace("USDT", "")
    raw = price_map.get(symbol)
    if raw is None:
        raw = price_map.get(base)
    try:
        px = float(raw)
        return px if math.isfinite(px) and px > 0 else None
    except (TypeError, ValueError):
        return None


def _price_for_action(price_map: Dict[str, float], symbol: str, action: str) -> Optional[float]:
    """Read an explicitly supplied paper fill price; None sentinel means unavailable."""
    base = symbol[:-5] if symbol.endswith("-USDT") else symbol.replace("USDT", "")
    keys = (f"{symbol}|{action}", f"{base}|{action}")
    for key in keys:
        if key in price_map:
            raw = price_map.get(key)
            try:
                value = float(raw)
                return value if math.isfinite(value) and value > 0 else None
            except (TypeError, ValueError):
                return None
    return _price_for(price_map, symbol)


def _mark_trailing(p: Dict[str, Any], price: float) -> bool:
    """Move a trailing stop monotonically after the configured activation move."""
    distance = float(p.get("trail_pct") or 0)
    if distance <= 0:
        return False
    side = p["side"]
    entry = float(p["entry"])
    best = float(p.get("best_price") or entry)
    active = bool(p.get("trail_active"))
    if side == "LONG":
        best = max(best, price)
        active = active or price >= entry * (1 + distance / 100.0)
        stop = best * (1 - distance / 100.0) if active else None
        old_stop = p.get("trail_stop")
        if old_stop is not None:
            stop = max(float(old_stop), stop) if stop is not None else float(old_stop)
    else:
        best = min(best, price)
        active = active or price <= entry * (1 - distance / 100.0)
        stop = best * (1 + distance / 100.0) if active else None
        old_stop = p.get("trail_stop")
        if old_stop is not None:
            stop = min(float(old_stop), stop) if stop is not None else float(old_stop)
    c = conn()
    try:
        c.execute("UPDATE positions SET best_price=?,trail_active=?,trail_stop=? WHERE id=? AND status='open'",
                  (best, int(active), stop, p["id"]))
        c.commit()
    finally:
        c.close()
    return bool(active and stop is not None and ((side == "LONG" and price <= stop) or (side == "SHORT" and price >= stop)))


def apply_tp_sl_trail(price_map: Dict[str, float]) -> List[str]:
    """Apply stop, trailing stop, isolated-demo liquidation, then staged take profits."""
    events: List[str] = []
    for p in list_open_positions():
        price = _price_for(price_map, p["symbol"])
        if price is None:
            continue
        pid, side, entry = p["id"], p["side"], float(p["entry"])
        exit_action = "SELL" if side == "LONG" else "BUY"
        exit_price = _price_for_action(price_map, p["symbol"], exit_action)
        if exit_price is None:
            # The quote supervisor explicitly marks missing executable BBOs.
            action_keys = (f"{p['symbol']}|{exit_action}",
                           f"{p['symbol'].replace('-USDT', '')}|{exit_action}")
            if any(key in price_map for key in action_keys):
                continue
            exit_price = price  # legacy direct calls with mark-only maps
        sl = p.get("sl")
        sl_hit = sl is not None and ((side == "LONG" and price <= float(sl)) or (side == "SHORT" and price >= float(sl)))
        if sl_hit:
            close_position(pid, exit_price, 100, "SL")
            events.append(f"حد ضرر پوزیشن #{pid}")
            continue
        # Approximate isolated-margin liquidation boundary: close at 90% margin loss.
        qty = float(p["qty_open"])
        margin = float(p.get("margin_open") or 0)
        adverse_move = (0.90 * margin / qty) if qty > 0 and margin > 0 else 0
        liq = (entry - adverse_move) if side == "LONG" else (entry + adverse_move)
        liq_hit = (price <= liq) if side == "LONG" else (price >= liq)
        if liq_hit and liq > 0:
            liquidation_fill = min(exit_price, liq) if side == "LONG" else max(exit_price, liq)
            close_position(pid, liquidation_fill, 100, "LIQUIDATION")
            events.append(f"بسته‌شدن اجباری دمو #{pid}")
            continue
        if _mark_trailing(p, price):
            close_position(pid, exit_price, 100, "TRAIL")
            events.append(f"حد ضرر متحرک پوزیشن #{pid}")
            continue
        # TP ladder: one target closes all; two targets split 50/50; three
        # targets close 30% / 30% / remaining 40% of the original size.
        p = next((x for x in list_open_positions() if x["id"] == pid), None)
        if not p:
            continue
        for target_key, flag_key in (("tp1", "tp1_hit"), ("tp2", "tp2_hit"), ("tp3", "tp3_hit")):
            p = next((x for x in list_open_positions() if x["id"] == pid), None)
            if not p:
                break
            target = p.get(target_key)
            if target is None or p.get(flag_key):
                continue
            hit = price >= float(target) if side == "LONG" else price <= float(target)
            if not hit:
                continue
            qty_now = float(p["qty_open"])
            original_qty = float(p["qty"])
            if target_key == "tp1" and p.get("tp2") is None:
                target_qty = qty_now
            elif target_key == "tp2" and p.get("tp3") is None:
                target_qty = qty_now
            elif target_key == "tp1" and p.get("tp2") is not None and p.get("tp3") is None:
                target_qty = original_qty * 0.50
            elif target_key == "tp1":
                target_qty = original_qty * 0.30
            elif target_key == "tp2":
                target_qty = original_qty * 0.30
            else:
                target_qty = qty_now
            close_qty = min(qty_now, target_qty)
            pct_current = min(100.0, close_qty / qty_now * 100.0)
            close_position(pid, exit_price, pct_current, target_key.upper())
            events.append(f"{target_key.upper()} پوزیشن #{pid}")
    return events


def add_note(title: str, body: str, symbol: str = "") -> None:
    c = conn()
    try:
        c.execute("INSERT INTO notes(title,body,symbol,ts) VALUES(?,?,?,?)",
                  (str(title or "یادداشت")[:80], str(body or "")[:4000], str(symbol or "")[:20], int(time.time())))
        c.commit()
    finally:
        c.close()


def list_notes(limit: int = 50) -> List[Dict[str, Any]]:
    c = conn()
    try:
        limit = max(1, min(500, int(limit)))
        return [dict(r) for r in c.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        c.close()


def list_trades(limit: int = 100) -> List[Dict[str, Any]]:
    c = conn()
    try:
        limit = max(1, min(1000, int(limit)))
        # Enrich each close with the originating position metadata so the
        # journal can compare manual and auto-demo flows without guessing.
        return [dict(r) for r in c.execute(
            """SELECT t.*, COALESCE(p.source,'manual') AS trade_source,
                      COALESCE(p.note,'') AS position_note
               FROM trades t LEFT JOIN positions p ON p.id=t.pos_id
               ORDER BY t.id DESC LIMIT ?""", (limit,)).fetchall()]
    finally:
        c.close()


def list_ledger(limit: int = 100) -> List[Dict[str, Any]]:
    c = conn()
    try:
        limit = max(1, min(2000, int(limit)))
        return [dict(r) for r in c.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        c.close()


def add_chat(role: str, content: str, model: str = "") -> None:
    c = conn()
    try:
        c.execute("INSERT INTO chat_log(role,content,model,ts) VALUES(?,?,?,?)",
                  (str(role)[:20], str(content)[:20000], str(model)[:80], int(time.time())))
        c.commit()
    finally:
        c.close()


def list_chat(limit: int = 40) -> List[Dict[str, Any]]:
    c = conn()
    try:
        limit = max(1, min(200, int(limit)))
        rows = c.execute("SELECT * FROM chat_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        c.close()


def export_report_csv() -> str:
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(["section", "field", "value"])
    for key, value in get_wallet().items():
        w.writerow(["wallet", key, value])
    for section, rows in (("trades", list_trades(1000)), ("open_positions", list_open_positions()),
                          ("pending_orders", list_pending_orders()), ("ledger", list_ledger(2000))):
        w.writerow([])
        w.writerow([section])
        if rows:
            w.writerow(list(rows[0].keys()))
            for row in rows:
                w.writerow([json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (dict, list)) else row.get(k, "") for k in rows[0].keys()])
    return buf.getvalue()


def equity_with_marks(price_map: Dict[str, float]) -> Dict[str, float]:
    w = get_wallet()
    upnl = 0.0
    locked = 0.0
    missing = []
    for p in list_open_positions():
        locked += float(p.get("margin_open") or 0)
        px = _price_for(price_map, p["symbol"])
        if px is None:
            missing.append(p["symbol"])
            continue
        upnl += position_pnl(p, px)["mtm_pnl"]
    c = conn()
    try:
        pending_reserved = _pending_reserved(c)
    finally:
        c.close()
    cash = float(w.get("balance") or 0)
    equity = cash + locked + upnl
    return {"balance": cash, "available_balance": max(0.0, cash - pending_reserved),
            "pending_reserved": pending_reserved, "locked_margin": locked,
            "unrealized": upnl, "equity": equity,
            "realized_pnl": float(w.get("realized_pnl") or 0),
            "total_deposited": float(w.get("total_deposited") or 0),
            "fees_paid": float(w.get("fees_paid") or 0), "missing_prices": len(missing)}


# --- User watchlist, one-shot price alerts, and demo risk sizing ---
def watchlist_add(symbol: str, note: str = "") -> None:
    normalized = _symbol(symbol)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute(
                "INSERT INTO watchlist(symbol,added_ts,note) VALUES(?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET added_ts=excluded.added_ts,note=excluded.note",
                (normalized, int(time.time()), str(note or "")[:100]),
            )
            c.commit()
        finally:
            c.close()


def watchlist_list(limit: int = 50) -> List[Dict[str, Any]]:
    c = conn()
    try:
        limit = max(1, min(100, int(limit)))
        return [dict(r) for r in c.execute(
            "SELECT * FROM watchlist ORDER BY added_ts DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        c.close()


def watchlist_remove(symbol: str) -> None:
    normalized = _symbol(symbol)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("DELETE FROM watchlist WHERE symbol=?", (normalized,))
            c.commit()
        finally:
            c.close()


def alert_add(symbol: str, condition: str, price: float, note: str = "") -> int:
    normalized = _symbol(symbol)
    condition = str(condition or "").lower().strip()
    if condition not in ("above", "below"):
        raise ValueError("شرط هشدار باید بالا یا پایین باشد")
    price = _number(price, "قیمت هشدار", minimum=0, allow_zero=False)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            count = int(c.execute("SELECT COUNT(*) FROM alerts WHERE status='open'").fetchone()[0])
            if count >= MAX_OPEN_ALERTS:
                raise ValueError(f"حداکثر {MAX_OPEN_ALERTS} هشدار باز مجاز است")
            cur = c.execute(
                "INSERT INTO alerts(symbol,condition,price,status,created_ts,note) "
                "VALUES(?,?,?,'open',?,?)",
                (normalized, condition, price, int(time.time()), str(note or "")[:100]),
            )
            c.commit()
            return int(cur.lastrowid)
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def alerts_open(limit: int = 100) -> List[Dict[str, Any]]:
    c = conn()
    try:
        limit = max(1, min(200, int(limit)))
        return [dict(r) for r in c.execute(
            "SELECT * FROM alerts WHERE status='open' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        c.close()


def alert_cancel(alert_id: int) -> bool:
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            cur = c.execute(
                "UPDATE alerts SET status='cancelled' WHERE id=? AND status='open'",
                (int(alert_id),),
            )
            changed = cur.rowcount > 0
            c.commit()
            return changed
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def process_alerts(price_map: Dict[str, float]) -> List[str]:
    """Trigger matching one-shot alerts only when a fresh caller-supplied price exists."""
    events: List[str] = []
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT * FROM alerts WHERE status='open' ORDER BY id").fetchall()
            for row in rows:
                price = _price_for(price_map, row["symbol"])
                if price is None:
                    continue
                hit = (row["condition"] == "above" and price >= float(row["price"])) or \
                      (row["condition"] == "below" and price <= float(row["price"]))
                if not hit:
                    continue
                cur = c.execute(
                    "UPDATE alerts SET status='triggered',triggered_ts=? WHERE id=? AND status='open'",
                    (int(time.time()), int(row["id"])),
                )
                if cur.rowcount:
                    message = f"هشدار #{row['id']} · {row['symbol']} · قیمت {price:g}"
                    events.append(message)
                    _notification_tx(c, "alert", "هشدار قیمت فعال شد", message)
            c.commit()
            return events
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def risk_size(balance: float, risk_pct: float, entry: float, sl: float,
              leverage: float = 1.0) -> Dict[str, float]:
    """Estimate paper position size including a round-trip fee buffer."""
    balance = _number(balance, "موجودی", minimum=0, allow_zero=False)
    risk_pct = _number(risk_pct, "درصد ریسک", minimum=0.1, maximum=3.0)
    entry = _number(entry, "قیمت ورود", minimum=0, allow_zero=False)
    sl = _number(sl, "حد ضرر", minimum=0, allow_zero=False)
    leverage = _number(leverage, "اهرم", minimum=1.0, maximum=MAX_LEVERAGE)
    distance = abs(entry - sl)
    if distance <= max(1e-12, entry * 1e-8):
        raise ValueError("فاصلهٔ ورود و حد ضرر کافی نیست")
    risk_budget = balance * risk_pct / 100.0
    per_unit_risk = distance + entry * FEE_RATE * 2.0
    quantity = risk_budget / per_unit_risk
    notional = quantity * entry
    margin = notional / leverage
    fee_buffer = notional * FEE_RATE * 2.0
    return {"risk_budget": risk_budget, "quantity": quantity, "notional": notional,
            "margin": margin, "fee_buffer": fee_buffer,
            "stop_distance_pct": distance / entry * 100.0,
            "risk_including_fee_buffer": quantity * distance + fee_buffer}


# ---- Separate spot paper wallet -------------------------------------------------
SPOT_FEE_RATE = 0.001


def get_spot_balances() -> List[Dict[str, Any]]:
    c = conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT asset,free,avg_cost,updated_ts FROM spot_balances WHERE free>0 ORDER BY CASE WHEN asset='USDT' THEN 0 ELSE 1 END, asset"
        ).fetchall()]
    finally:
        c.close()


def spot_deposit(amount: float, note: str = "شارژ کیف پول اسپات دمو") -> Dict[str, float]:
    amount = _number(amount, "مبلغ شارژ دمو", minimum=0, allow_zero=False)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT free FROM spot_balances WHERE asset='USDT'").fetchone()
            balance = float(row[0] if row else 0.0) + amount
            now = int(time.time())
            c.execute("INSERT INTO spot_balances(asset,free,avg_cost,updated_ts) VALUES('USDT',?,1,?) "
                      "ON CONFLICT(asset) DO UPDATE SET free=excluded.free,updated_ts=excluded.updated_ts",
                      (balance, now))
            c.execute("INSERT INTO spot_ledger(asset,type,amount,balance_after,note,ts) VALUES('USDT','deposit',?,?,?,?)",
                      (amount, balance, str(note)[:160], now))
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    return spot_summary({})


def spot_withdraw(amount: float, note: str = "کاهش موجودی مجازی اسپات") -> Dict[str, float]:
    amount = _number(amount, "مبلغ کاهش موجودی", minimum=0, allow_zero=False)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT free FROM spot_balances WHERE asset='USDT'").fetchone()
            balance = float(row[0] if row else 0.0)
            if amount > balance + 1e-9:
                raise ValueError(f"موجودی USDT اسپات کافی نیست؛ موجودی {balance:.4f}")
            remaining = max(0.0, balance - amount)
            now = int(time.time())
            c.execute("UPDATE spot_balances SET free=?,updated_ts=? WHERE asset='USDT'", (remaining, now))
            c.execute("INSERT INTO spot_ledger(asset,type,amount,balance_after,note,ts) VALUES('USDT','withdraw',?,?,?,?)",
                      (-amount, remaining, str(note)[:160], now))
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    return spot_summary({})


def spot_market_order(symbol: str, side: str, amount: float, price: float,
                      fee_rate: float = SPOT_FEE_RATE, note: str = "سفارش دستی اسپات دمو") -> Dict[str, Any]:
    """Execute a simulated spot market order at a caller-supplied fresh mark.

    BUY amount is quote USDT. SELL amount is base-asset quantity.
    No exchange order endpoint or real-funds path exists in this function.
    """
    symbol = _symbol(symbol)
    base = symbol[:-5] if symbol.endswith('-USDT') else symbol
    side = str(side or '').upper().strip()
    if side not in ('BUY', 'SELL'):
        raise ValueError('سمت اسپات باید BUY یا SELL باشد')
    amount = _number(amount, 'مقدار سفارش', minimum=0, allow_zero=False)
    price = _number(price, 'قیمت بازار', minimum=0, allow_zero=False)
    fee_rate = _number(fee_rate, 'کارمزد', minimum=0, maximum=0.02)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute('BEGIN IMMEDIATE')
            now = int(time.time())
            qrow = c.execute("SELECT free FROM spot_balances WHERE asset='USDT'").fetchone()
            usdt = float(qrow[0] if qrow else 0.0)
            brow = c.execute('SELECT free,avg_cost FROM spot_balances WHERE asset=?', (base,)).fetchone()
            base_free = float(brow['free']) if brow else 0.0
            avg_cost = float(brow['avg_cost']) if brow else 0.0
            if side == 'BUY':
                quote = amount
                if quote > usdt + 1e-9:
                    raise ValueError(f'موجودی USDT اسپات کافی نیست؛ موجودی {usdt:.4f}')
                fee = quote * fee_rate
                qty = (quote - fee) / price
                if qty <= 0:
                    raise ValueError('حجم خرید پس از کارمزد صفر است')
                new_base = base_free + qty
                new_avg = ((base_free * avg_cost) + quote) / new_base if new_base > 0 else 0.0
                new_usdt = max(0.0, usdt - quote)
                realized = 0.0
                c.execute("INSERT INTO spot_balances(asset,free,avg_cost,updated_ts) VALUES(?,?,?,?) "
                          "ON CONFLICT(asset) DO UPDATE SET free=excluded.free,avg_cost=excluded.avg_cost,updated_ts=excluded.updated_ts",
                          (base, new_base, new_avg, now))
            else:
                qty = amount
                if qty > base_free + 1e-9:
                    raise ValueError(f'موجودی {base} کافی نیست؛ موجودی {base_free:.8g}')
                quote = qty * price
                fee = quote * fee_rate
                net = quote - fee
                new_base = max(0.0, base_free - qty)
                new_usdt = usdt + net
                realized = (price - avg_cost) * qty - fee
                c.execute('UPDATE spot_balances SET free=?,avg_cost=?,updated_ts=? WHERE asset=?',
                          (new_base, avg_cost if new_base > 1e-12 else 0.0, now, base))
            c.execute("INSERT INTO spot_balances(asset,free,avg_cost,updated_ts) VALUES('USDT',?,1,?) "
                      "ON CONFLICT(asset) DO UPDATE SET free=excluded.free,updated_ts=excluded.updated_ts",
                      (new_usdt, now))
            cur = c.execute("INSERT INTO spot_trades(symbol,side,quantity,price,quote_amount,fee,realized_pnl,ts,note) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (symbol, side, qty, price, quote, fee, realized, now, str(note)[:160]))
            c.execute("INSERT INTO spot_ledger(asset,type,amount,balance_after,note,ts) VALUES(?,?,?,?,?,?)",
                      ('USDT', 'trade_'+side.lower(), ( -quote if side=='BUY' else quote-fee), new_usdt,
                       f'{symbol} · {side} · #{cur.lastrowid}', now))
            c.commit()
            return {'id': int(cur.lastrowid), 'symbol': symbol, 'side': side,
                    'quantity': qty, 'price': price, 'quote_amount': quote,
                    'fee': fee, 'realized_pnl': realized,
                    'usdt_balance': new_usdt, 'base_balance': new_base,
                    'avg_cost': new_avg if side == 'BUY' else (avg_cost if new_base > 1e-12 else 0.0)}
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def spot_summary(price_map: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    price_map = price_map or {}
    balances = get_spot_balances()
    total = 0.0
    missing = []
    rows = []
    for row in balances:
        asset = row['asset']
        qty = float(row['free'])
        if asset == 'USDT':
            value = qty
            price = 1.0
        else:
            price = price_map.get(asset) or price_map.get(f'{asset}-USDT')
            if not price or float(price) <= 0:
                value = None
                missing.append(asset)
            else:
                value = qty * float(price)
        if value is not None:
            total += value
        rows.append({**row, 'price': price, 'value': value})
    return {'balances': rows, 'total_known_value': total, 'missing_prices': missing}


def spot_trades(limit: int = 200, since_ts: Optional[int] = None) -> List[Dict[str, Any]]:
    limit = max(1, min(5000, int(limit)))
    c = conn()
    try:
        if since_ts is None:
            rows = c.execute('SELECT * FROM spot_trades ORDER BY ts DESC,id DESC LIMIT ?', (limit,)).fetchall()
        else:
            rows = c.execute('SELECT * FROM spot_trades WHERE ts>=? ORDER BY ts DESC,id DESC LIMIT ?',
                             (int(since_ts), limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def report_period_seconds(period: str) -> Optional[int]:
    values = {'1d': 86400, '3d': 3*86400, '1w': 7*86400,
              '1m': 30*86400, '1y': 365*86400, 'all': None}
    key = str(period or 'all').lower().strip()
    if key not in values:
        raise ValueError('بازهٔ گزارش نامعتبر است')
    return values[key]


def report_trades(period: str = 'all', now: Optional[int] = None) -> List[Dict[str, Any]]:
    seconds = report_period_seconds(period)
    limit = 5000
    if seconds is None:
        return list_trades(limit)
    stamp = int(now if now is not None else time.time()) - seconds
    c = conn()
    try:
        return [dict(r) for r in c.execute('SELECT * FROM trades WHERE ts>=? ORDER BY ts DESC,id DESC LIMIT ?',
                                          (stamp, limit)).fetchall()]
    finally:
        c.close()


def export_period_csv(period: str = 'all') -> str:
    import csv, io
    rows = report_trades(period)
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['id','symbol','side','entry','exit','qty','leverage','pnl','pnl_pct','entry_fee','fee','reason','ts'])
    for row in rows:
        writer.writerow([row.get(k) for k in ('id','symbol','side','entry','exit','qty','leverage','pnl','pnl_pct','entry_fee','fee','reason','ts')])
    return out.getvalue()


def add_position_margin(position_id: int, amount: float) -> Dict[str, Any]:
    """Top up isolated margin on an open demo position from free demo cash."""
    amount = _number(amount, "افزایش مارجین", minimum=0, allow_zero=False)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            position = c.execute("SELECT * FROM positions WHERE id=? AND status='open'",
                                 (int(position_id),)).fetchone()
            if not position:
                raise ValueError("پوزیشن باز پیدا نشد")
            wallet = _wallet_row(c)
            balance = float(wallet["balance"])
            pending = _pending_reserved(c)
            available = max(0.0, balance - pending)
            if amount > available + 1e-9:
                raise ValueError(f"موجودی آزاد کافی نیست؛ موجودی قابل استفاده {available:.4f} USDT")
            new_balance = balance - amount
            new_margin = float(position["margin_open"] or position["margin"] or 0.0) + amount
            c.execute("UPDATE positions SET margin=?,margin_open=? WHERE id=? AND status='open'",
                      (new_margin, new_margin, int(position_id)))
            _wallet_update(c, new_balance)
            _ledger(c, "margin_add", -amount, new_balance,
                    f"افزایش مارجین پوزیشن دمو #{int(position_id)}")
            c.commit()
            return {"position_id": int(position_id), "added": amount,
                    "margin_open": new_margin, "balance": new_balance}
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def update_position_risk_levels(position_id: int, tp1=None, tp2=None, tp3=None,
                                sl=None, trail_pct=None) -> Dict[str, Any]:
    """Validate and update take-profit/stop-loss settings on an open demo position."""
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM positions WHERE id=? AND status='open'",
                            (int(position_id),)).fetchone()
            if not row:
                raise ValueError("پوزیشن باز پیدا نشد")
            p = dict(row)
            a, b, d, stop = _check_levels(p["side"], float(p["entry"]), tp1, tp2, tp3, sl)
            trail = float(p.get("trail_pct") or 0) if trail_pct in (None, "") else _number(
                trail_pct, "فاصلهٔ تریل", minimum=0, maximum=MAX_TRAIL_PCT)
            c.execute("""UPDATE positions SET tp1=?,tp2=?,tp3=?,sl=?,trail_pct=?,
                         trail_active=0,trail_stop=NULL,best_price=? WHERE id=? AND status='open'""",
                      (a, b, d, stop, trail, float(p["entry"]), int(position_id)))
            c.commit()
            return {"position_id": int(position_id), "tp1": a, "tp2": b,
                    "tp3": d, "sl": stop, "trail_pct": trail}
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


# --- KALVO notification inbox and advanced one-shot percentage alerts --------
def _notification_tx(c: sqlite3.Connection, category: str, title: str, body: str) -> int:
    cur = c.execute(
        "INSERT INTO notifications(category,title,body,created_ts) VALUES(?,?,?,?)",
        (str(category or "system")[:24], str(title or "اعلان")[:100], str(body or "")[:500], int(time.time())),
    )
    return int(cur.lastrowid)


def notification_add(category: str, title: str, body: str) -> int:
    # Persist the inbox entry first. Android notification failures must never
    # roll back or interrupt the local paper-trading ledger.
    with _DB_LOCK:
        c = conn()
        try:
            row_id = _notification_tx(c, category, title, body)
            c.commit()
        finally:
            c.close()
    try:
        if get_setting("system_notifications_enabled", "1") == "1":
            from . import platform_notifications
            platform_notifications.notify(
                platform_notifications.notification_id_for(category, title, body, row_id),
                title, body,
            )
    except Exception:
        # System notification delivery is best-effort; the in-app inbox remains authoritative.
        pass
    return row_id


def notifications_list(limit: int = 60, unread_only: bool = False) -> List[Dict[str, Any]]:
    limit = max(1, min(300, int(limit)))
    c = conn()
    try:
        sql = "SELECT * FROM notifications" + (" WHERE read_ts IS NULL" if unread_only else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in c.execute(sql, (limit,)).fetchall()]
    finally:
        c.close()


def notifications_mark_read(notification_id: Optional[int] = None) -> int:
    with _DB_LOCK:
        c = conn()
        try:
            if notification_id is None:
                cur = c.execute("UPDATE notifications SET read_ts=? WHERE read_ts IS NULL", (int(time.time()),))
            else:
                cur = c.execute("UPDATE notifications SET read_ts=? WHERE id=? AND read_ts IS NULL",
                                (int(time.time()), int(notification_id)))
            c.commit()
            return int(cur.rowcount)
        finally:
            c.close()


def smart_alert_add(symbol: str, condition: str, threshold: float, note: str = "") -> int:
    normalized = _symbol(symbol)
    condition = str(condition or "").lower().strip()
    if condition not in ("above", "below"):
        raise ValueError("شرط هشدار نامعتبر است")
    threshold = _number(threshold, "درصد تغییر", minimum=-100, maximum=1000)
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            count = int(c.execute("SELECT COUNT(*) FROM smart_alerts WHERE status='open'").fetchone()[0])
            if count >= 50:
                raise ValueError("حداکثر ۵۰ هشدار پیشرفتهٔ باز مجاز است")
            cur = c.execute(
                "INSERT INTO smart_alerts(symbol,metric,condition,threshold,status,created_ts,note) VALUES(?,'change_pct',?,?,'open',?,?)",
                (normalized, condition, threshold, int(time.time()), str(note or "")[:100]),
            )
            c.commit()
            return int(cur.lastrowid)
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def smart_alerts_open(limit: int = 100) -> List[Dict[str, Any]]:
    c = conn()
    try:
        limit = max(1, min(200, int(limit)))
        return [dict(r) for r in c.execute(
            "SELECT * FROM smart_alerts WHERE status='open' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        c.close()


def smart_alert_cancel(alert_id: int) -> bool:
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            cur = c.execute("UPDATE smart_alerts SET status='cancelled' WHERE id=? AND status='open'",
                            (int(alert_id),))
            c.commit()
            return cur.rowcount == 1
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def process_smart_alerts(snapshots: Dict[str, Dict[str, Any]], max_age: int = 90) -> List[str]:
    """Evaluate 24h percentage alerts only against fresh public ticker snapshots."""
    events = []
    now = time.time()
    with _DB_LOCK:
        c = conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT * FROM smart_alerts WHERE status='open' ORDER BY id").fetchall()
            for row in rows:
                snap = snapshots.get(row["symbol"]) or snapshots.get(row["symbol"].replace("-USDT", ""))
                if not isinstance(snap, dict):
                    continue
                try:
                    ts = float(snap.get("ts") or 0)
                    value = float(snap.get("change_pct"))
                    if not math.isfinite(value) or ts <= 0 or now - ts > max_age or ts - now > 10:
                        continue
                except (TypeError, ValueError):
                    continue
                threshold = float(row["threshold"])
                hit = value >= threshold if row["condition"] == "above" else value <= threshold
                if not hit:
                    continue
                cur = c.execute("UPDATE smart_alerts SET status='triggered',triggered_ts=? WHERE id=? AND status='open'",
                                (int(now), int(row["id"])))
                if cur.rowcount:
                    message = f"{row['symbol']} · تغییر ۲۴ساعته {value:+.2f}% · آستانه {threshold:+.2f}%"
                    events.append(message)
                    _notification_tx(c, "smart_alert", "هشدار درصد تغییر فعال شد", message)
            c.commit()
            return events
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def wallet_snapshot() -> Dict[str, float]:
    """Compact wallet view for AI context."""
    try:
        w = get_wallet()
        return {
            "equity": w.get("equity") or w.get("balance"),
            "free": w.get("free") or w.get("balance"),
            "locked": w.get("locked_margin") or w.get("locked") or 0,
            "balance": w.get("balance"),
        }
    except Exception:
        return {}



def _after_trade_closed() -> None:
    """Keep adaptive memory in sync whenever a demo trade fully/partially settles."""
    try:
        from . import learning_memory as _lm
        _lm.refresh_if_new_trades()
    except Exception:
        pass
    try:
        from . import ai_core as _core
        # force scorecard refresh side-effect via recent log read
        _core.model_scorecard()
    except Exception:
        pass
