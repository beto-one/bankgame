"""منطق بانک بازی (SQLite). تمام جابه‌جایی پول داخل یک تراکنش اتمیک انجام می‌شود."""
import hashlib, hmac, re, secrets, sqlite3, time
from contextlib import contextmanager

START_BALANCE = 100
MAX_BAL = 1_000_000_000
MAX_FAILS = 3            # بعد از ۳ رمز اشتباه، حساب قفل می‌شود (ادمین باز می‌کند)
SESSION_TTL = 180        # ثانیه (با هر عمل تمدید می‌شود)
ADMIN_TTL = 3600

class BankError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code); self.code = code; self.status = status; self.commit = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  pin_hash TEXT NOT NULL, pin_salt TEXT NOT NULL,
  balance INTEGER NOT NULL CHECK(balance >= 0), fails INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS txs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, acc INTEGER NOT NULL, other INTEGER, kind TEXT NOT NULL,
  amount INTEGER NOT NULL, balance_after INTEGER NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS txs_acc ON txs(acc, id);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, acc INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_sessions(token TEXT PRIMARY KEY, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v REAL NOT NULL);
"""

def connect(path):
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db

@contextmanager
def tx(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BankError as e:
        db.execute("COMMIT" if e.commit else "ROLLBACK"); raise   # رمز غلط باید ثبت شود، پس commit
    except BaseException:
        db.execute("ROLLBACK"); raise
    else:
        db.execute("COMMIT")

def _is_int(x): return isinstance(x, int) and not isinstance(x, bool)
def _hash(pin, salt): return hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(salt), 20000).hex()
def norm_uid(uid):
    u = re.sub(r"[^0-9A-Fa-f]", "", str(uid or "")).upper()
    if not 8 <= len(u) <= 20: raise BankError("bad_uid")
    return u
def _clean_name(n):
    n = str(n or "").strip()
    if not 1 <= len(n) <= 20 or any(ord(c) < 32 for c in n): raise BankError("bad_name")
    return n
def _valid_pin(p):
    if not isinstance(p, str) or not re.fullmatch(r"\d{4}", p): raise BankError("bad_pin_format")

def _check_pin(db, acc, pin):
    r = db.execute("SELECT pin_hash,pin_salt,fails FROM accounts WHERE id=?", (acc,)).fetchone()
    if not r: raise BankError("no_account", 404)
    if r["fails"] >= MAX_FAILS: raise BankError("locked", 423)
    if not isinstance(pin, str) or not hmac.compare_digest(_hash(pin, r["pin_salt"]), r["pin_hash"]):
        db.execute("UPDATE accounts SET fails=fails+1 WHERE id=?", (acc,))
        e = BankError("wrong_pin", 401); e.commit = True; raise e
    if r["fails"]: db.execute("UPDATE accounts SET fails=0 WHERE id=?", (acc,))

def _log(db, acc, other, kind, amount, bal):
    db.execute("INSERT INTO txs(acc,other,kind,amount,balance_after,ts) VALUES(?,?,?,?,?,?)",
               (acc, other, kind, amount, bal, time.time()))

# ---------- حساب‌ها ----------
def tap(db, uid):
    r = db.execute("SELECT id,name FROM accounts WHERE uid=?", (norm_uid(uid),)).fetchone()
    return {"known": bool(r), "acc": r["id"] if r else None, "name": r["name"] if r else None}

def register(db, uid, name, pin):
    uid = norm_uid(uid); name = _clean_name(name); _valid_pin(pin)
    salt = secrets.token_hex(8)
    with tx(db):
        if db.execute("SELECT 1 FROM accounts WHERE uid=?", (uid,)).fetchone(): raise BankError("card_exists", 409)
        cur = db.execute("INSERT INTO accounts(uid,name,pin_hash,pin_salt,balance,created) VALUES(?,?,?,?,?,?)",
                         (uid, name, _hash(pin, salt), salt, START_BALANCE, time.time()))
        _log(db, cur.lastrowid, None, "open", START_BALANCE, START_BALANCE)
    return cur.lastrowid

def login(db, pin, acc=None, uid=None):
    if acc is None and uid is not None:
        r = db.execute("SELECT id FROM accounts WHERE uid=?", (norm_uid(uid),)).fetchone()
        if not r: raise BankError("no_account", 404)
        acc = r["id"]
    if not _is_int(acc): raise BankError("no_account", 404)
    token = secrets.token_hex(16)
    with tx(db):
        _check_pin(db, acc, pin)
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES(?,?,?)", (token, acc, time.time() + SESSION_TTL))
    return token

def session(db, token):
    r = db.execute("SELECT acc,expires FROM sessions WHERE token=?", (str(token or ""),)).fetchone()
    if not r or r["expires"] < time.time(): raise BankError("auth", 401)
    db.execute("UPDATE sessions SET expires=? WHERE token=?", (time.time() + SESSION_TTL, token))
    return r["acc"]

def logout(db, token): db.execute("DELETE FROM sessions WHERE token=?", (str(token or ""),))

def me(db, token):
    acc = session(db, token)
    a = db.execute("SELECT id,name,balance FROM accounts WHERE id=?", (acc,)).fetchone()
    if not a: raise BankError("auth", 401)
    txs = db.execute("""SELECT t.kind,t.other,t.amount,t.balance_after,t.ts,o.name AS other_name FROM txs t
        LEFT JOIN accounts o ON o.id=t.other WHERE t.acc=? ORDER BY t.id DESC LIMIT 20""", (acc,)).fetchall()
    return {"id": a["id"], "name": a["name"], "balance": a["balance"], "txs": [dict(t) for t in txs]}

def board(db):
    return [dict(r) for r in db.execute("SELECT id,name,balance FROM accounts ORDER BY balance DESC,id LIMIT 50")]

def transfer(db, token, to_acc, amount, pin):
    """کارت به کارت: هم از حساب فرستنده کم می‌شود، هم به گیرنده اضافه؛ هر دو در یک تراکنش."""
    if not _is_int(amount) or not 0 < amount <= MAX_BAL: raise BankError("bad_amount")
    if not _is_int(to_acc): raise BankError("no_account", 404)
    me_id = session(db, token)
    if me_id == to_acc: raise BankError("same_account")
    with tx(db):
        dst = db.execute("SELECT balance FROM accounts WHERE id=?", (to_acc,)).fetchone()
        src = db.execute("SELECT balance FROM accounts WHERE id=?", (me_id,)).fetchone()
        if not dst: raise BankError("no_account", 404)
        if not src: raise BankError("auth", 401)
        _check_pin(db, me_id, pin)
        if src["balance"] < amount: raise BankError("insufficient", 402)
        if dst["balance"] + amount > MAX_BAL: raise BankError("bad_amount")
        n = db.execute("UPDATE accounts SET balance=balance-? WHERE id=? AND balance>=?", (amount, me_id, amount)).rowcount
        if n != 1: raise BankError("insufficient", 402)
        db.execute("UPDATE accounts SET balance=balance+? WHERE id=?", (amount, to_acc))
        sb = src["balance"] - amount; db_ = dst["balance"] + amount
        _log(db, me_id, to_acc, "out", amount, sb); _log(db, to_acc, me_id, "in", amount, db_)
    return {"balance": sb}

# ---------- ادمین ----------
def admin_login(db, given, expected):
    now = time.time()
    lock = db.execute("SELECT v FROM meta WHERE k='admin_lock'").fetchone()
    if lock and lock["v"] > now: raise BankError("admin_locked", 429)
    if not expected or not hmac.compare_digest(str(given or "").encode(), expected.encode()):
        f = db.execute("SELECT v FROM meta WHERE k='admin_fails'").fetchone()
        n = (f["v"] if f else 0) + 1
        if n >= 5:
            db.execute("REPLACE INTO meta VALUES('admin_lock',?)", (now + 30,)); n = 0
        db.execute("REPLACE INTO meta VALUES('admin_fails',?)", (n,))
        raise BankError("admin_auth", 401)
    db.execute("REPLACE INTO meta VALUES('admin_fails',0)")
    t = secrets.token_hex(16)
    db.execute("DELETE FROM admin_sessions WHERE expires<?", (now,))
    db.execute("INSERT INTO admin_sessions VALUES(?,?)", (t, now + ADMIN_TTL))
    return t

def admin_check(db, token):
    r = db.execute("SELECT expires FROM admin_sessions WHERE token=?", (str(token or ""),)).fetchone()
    if not r or r["expires"] < time.time(): raise BankError("admin_auth", 401)

def admin_accounts(db):
    return [dict(r) for r in db.execute("SELECT id,name,uid,balance,fails FROM accounts ORDER BY id")]

def admin_cmd(db, op, acc=None, val=None):
    """op: add | set | unlock | pin | delete | reset   (ادمین بدون محدودیت)"""
    with tx(db):
        if op == "reset":
            for t in ("txs", "sessions", "accounts"): db.execute(f"DELETE FROM {t}")
            return {}
        r = db.execute("SELECT balance FROM accounts WHERE id=?", (acc,)).fetchone() if _is_int(acc) else None
        if not r: raise BankError("no_account", 404)
        if op in ("add", "set"):
            if not _is_int(val): raise BankError("bad_amount")
            new = r["balance"] + val if op == "add" else val
            if op == "set" and not 0 <= val <= MAX_BAL: raise BankError("bad_amount")
            new = max(0, min(MAX_BAL, new))
            db.execute("UPDATE accounts SET balance=? WHERE id=?", (new, acc))
            _log(db, acc, None, "admin", new - r["balance"], new)
            return {"balance": new}
        if op == "unlock": db.execute("UPDATE accounts SET fails=0 WHERE id=?", (acc,)); return {}
        if op == "pin":
            _valid_pin(val); salt = secrets.token_hex(8)
            db.execute("UPDATE accounts SET pin_hash=?,pin_salt=?,fails=0 WHERE id=?", (_hash(val, salt), salt, acc))
            db.execute("DELETE FROM sessions WHERE acc=?", (acc,)); return {}
        if op == "delete":
            for q in ("DELETE FROM sessions WHERE acc=?", "DELETE FROM txs WHERE acc=?", "DELETE FROM accounts WHERE id=?"):
                db.execute(q, (acc,))
            return {}
        raise BankError("bad_cmd")
