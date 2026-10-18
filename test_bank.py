import os, random, tempfile, threading, time, unittest
os.environ["TERMINAL_KEY"] = "tk"; os.environ["ADMIN_PASS"] = "adm"
import bank, app as appmod

def newdb():
    p = tempfile.mktemp(suffix=".db"); return p, bank.connect(p)
def total(db): return db.execute("SELECT COALESCE(SUM(balance),0) s FROM accounts").fetchone()["s"]
def mk(db, n, bal=None):
    ids = []
    for i in range(n):
        ids.append(bank.register(db, "A1B2C3D%d" % (i + 10), "u%d" % i, "%04d" % (1000 + i)))
    if bal is not None: [db.execute("UPDATE accounts SET balance=?", (bal,))]
    return ids
def code(fn, *a, **k):
    try: fn(*a, **k); return "ok"
    except bank.BankError as e: return e.code

class Core(unittest.TestCase):
    def test_register_and_uid(self):
        p, db = newdb(); a = bank.register(db, "04:a1:b2:c3", "Ali", "1234")
        self.assertEqual(db.execute("SELECT balance FROM accounts WHERE id=?", (a,)).fetchone()[0], 100)
        self.assertEqual(bank.tap(db, "04A1B2C3")["acc"], a)                 # فرمت‌های مختلف UID یکی می‌شوند
        self.assertEqual(code(bank.register, db, "04A1B2C3", "X", "1234"), "card_exists")
        self.assertEqual(code(bank.register, db, "zz", "X", "1234"), "bad_uid")
        self.assertEqual(code(bank.register, db, "04A1B2C4", "", "1234"), "bad_name")
        self.assertEqual(code(bank.register, db, "04A1B2C4", "X", "12"), "bad_pin_format")
        self.assertEqual(code(bank.register, db, "04A1B2C4", "X", 1234), "bad_pin_format")
        self.assertFalse(bank.tap(db, "FFFFFFFF")["known"])

    def test_transfer_debits_sender(self):
        p, db = newdb(); a, b = mk(db, 2)
        t = bank.login(db, "1000", acc=a); T = total(db)
        self.assertEqual(bank.transfer(db, t, b, 30, "1000")["balance"], 70)
        bal = lambda i: db.execute("SELECT balance FROM accounts WHERE id=?", (i,)).fetchone()[0]
        self.assertEqual((bal(a), bal(b), total(db)), (70, 130, T))          # فرستنده کم، گیرنده زیاد، جمع ثابت
        # دو سطر تاریخچه
        self.assertEqual(db.execute("SELECT COUNT(*) FROM txs WHERE kind IN('out','in')").fetchone()[0], 2)

    def test_transfer_rejections_change_nothing(self):
        p, db = newdb(); a, b = mk(db, 2); t = bank.login(db, "1000", acc=a); T = total(db)
        snap = lambda: [tuple(r) for r in db.execute("SELECT id,balance FROM accounts")]; s0 = snap()
        cases = [(b, 101, "1000", "insufficient"), (b, 0, "1000", "bad_amount"), (b, -5, "1000", "bad_amount"),
                 (b, True, "1000", "bad_amount"), (b, 1.5, "1000", "bad_amount"), (b, "5", "1000", "bad_amount"),
                 (a, 5, "1000", "same_account"), (999, 5, "1000", "no_account"), ("x", 5, "1000", "no_account"),
                 (b, 5, "9999", "wrong_pin"), (b, 5, None, "wrong_pin")]
        for to, amt, pin, exp in cases: self.assertEqual(code(bank.transfer, db, t, to, amt, pin), exp, (to, amt, pin))
        self.assertEqual(snap(), s0); self.assertEqual(total(db), T)
        self.assertEqual(code(bank.transfer, db, "badtoken", b, 5, "1000"), "auth")

    def test_pin_lockout(self):
        p, db = newdb(); a, = mk(db, 1)
        for _ in range(3): self.assertEqual(code(bank.login, db, "0000", acc=a), "wrong_pin")
        self.assertEqual(code(bank.login, db, "1000", acc=a), "locked")         # رمز درست هم بعد از قفل جواب نمی‌دهد
        bank.admin_cmd(db, "unlock", a); self.assertEqual(code(bank.login, db, "1000", acc=a), "ok")

    def test_transfer_wrong_pin_counts_and_locks(self):
        p, db = newdb(); a, b = mk(db, 2); t = bank.login(db, "1000", acc=a)
        for _ in range(3): self.assertEqual(code(bank.transfer, db, t, b, 5, "1"), "wrong_pin")
        self.assertEqual(code(bank.transfer, db, t, b, 5, "1000"), "locked")

    def test_session_expiry(self):
        p, db = newdb(); a, = mk(db, 1); t = bank.login(db, "1000", acc=a)
        db.execute("UPDATE sessions SET expires=?", (time.time() - 1,)); self.assertEqual(code(bank.me, db, t), "auth")

    def test_admin(self):
        p, db = newdb(); a, b = mk(db, 2)
        self.assertEqual(code(bank.admin_login, db, "bad", "adm"), "admin_auth")
        t = bank.admin_login(db, "adm", "adm"); bank.admin_check(db, t)
        self.assertEqual(code(bank.admin_check, db, "nope"), "admin_auth")
        self.assertEqual(bank.admin_cmd(db, "add", a, 5000)["balance"], 5100)
        self.assertEqual(bank.admin_cmd(db, "add", a, -99999)["balance"], 0)       # از صفر پایین‌تر نمی‌رود
        self.assertEqual(bank.admin_cmd(db, "set", a, 77)["balance"], 77)
        self.assertEqual(code(bank.admin_cmd, db, "set", a, -1), "bad_amount")
        self.assertEqual(code(bank.admin_cmd, db, "add", 99, 1), "no_account")
        self.assertEqual(code(bank.admin_cmd, db, "nope", a, 1), "bad_cmd")
        bank.admin_cmd(db, "pin", a, "4321"); self.assertEqual(code(bank.login, db, "4321", acc=a), "ok")
        self.assertEqual(code(bank.admin_cmd, db, "pin", a, "12"), "bad_pin_format")
        bank.admin_cmd(db, "delete", a); self.assertEqual(code(bank.login, db, "4321", acc=a), "no_account")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM txs WHERE acc=?", (a,)).fetchone()[0], 0)
        c = bank.register(db, "A1B2C3D0", "new", "1111"); self.assertNotEqual(c, a)   # شماره قبلی دوباره استفاده نمی‌شود
        bank.admin_cmd(db, "reset"); self.assertEqual(total(db), 0)
        for _ in range(5): code(bank.admin_login, db, "bad", "adm")
        self.assertEqual(code(bank.admin_login, db, "adm", "adm"), "admin_locked")   # قفل ۳۰ ثانیه‌ای

    def test_random_conservation(self):
        p, db = newdb(); ids = mk(db, 5); T = total(db); rnd = random.Random(1)
        toks = {i: bank.login(db, "%04d" % (1000 + k), acc=i) for k, i in enumerate(ids)}
        for _ in range(1500):
            f, t = rnd.choice(ids), rnd.choice(ids)
            code(bank.transfer, db, toks[f], t, rnd.randint(-5, 130), "%04d" % (1000 + ids.index(f)))
            self.assertEqual(total(db), T)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM accounts WHERE balance<0").fetchone()[0], 0)

    def test_threads(self):
        p, db = newdb(); ids = mk(db, 4); T = total(db); errs = []
        def work(k):
            try:
                d = bank.connect(p); tok = bank.login(d, "%04d" % (1000 + k), acc=ids[k]); r = random.Random(k)
                for _ in range(120): code(bank.transfer, d, tok, r.choice(ids), r.randint(1, 60), "%04d" % (1000 + k))
            except Exception as e: errs.append(repr(e))
        ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, []); self.assertEqual(total(db), T)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM accounts WHERE balance<0").fetchone()[0], 0)

class Http(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mktemp(suffix=".db"); appmod.app.config["DB_PATH"] = self.p; self.c = appmod.app.test_client()
        self.tk = {"X-Terminal-Key": "tk"}
    def post(self, u, j, h=None): return self.c.post(u, json=j, headers=h or {})
    def test_flow(self):
        self.assertEqual(self.post("/api/tap", {"uid": "04A1B2C3"}).status_code, 403)        # بدون کلید ترمینال
        self.assertEqual(self.post("/api/tap", {"uid": "04A1B2C3"}, self.tk).json["known"], False)
        r = self.post("/api/register", {"uid": "04A1B2C3", "name": "Ali", "pin": "1234"}, self.tk); self.assertEqual(r.status_code, 200)
        b = self.post("/api/register", {"uid": "04A1B2C4", "name": "Sara", "pin": "4321"}, self.tk).json["acc"]
        self.assertEqual(self.post("/api/login", {"uid": "04A1B2C3", "pin": "1234"}).status_code, 403)   # ورود با uid بدون کلید
        self.assertEqual(self.post("/api/login", {"acc": 1, "pin": "0000"}).status_code, 401)
        tok = self.post("/api/login", {"uid": "04A1B2C3", "pin": "1234"}, self.tk).json["token"]; H = {"Authorization": "Bearer " + tok}
        self.assertEqual(self.c.get("/api/me", headers=H).json["balance"], 100)
        self.assertEqual(self.c.get("/api/me").status_code, 401)
        self.assertEqual(self.post("/api/transfer", {"to": b, "amount": 40, "pin": "1234"}, H).json["balance"], 60)
        self.assertEqual(self.post("/api/transfer", {"to": b, "amount": 61, "pin": "1234"}, H).status_code, 402)
        board = {x["name"]: x["balance"] for x in self.c.get("/api/board").json}; self.assertEqual(board, {"Ali": 60, "Sara": 140})
        self.assertEqual(self.c.post("/api/register", json={"uid": "A" * 9000}, headers=self.tk).status_code, 413)
        self.assertEqual(self.c.post("/api/login", data="not json").status_code, 404)   # بدنه خراب: حساب پیدا نمی‌شود، کرش نمی‌کند
        self.assertEqual(self.c.get("/").status_code, 200)
    def test_admin_http(self):
        self.post("/api/register", {"uid": "04A1B2C3", "name": "Ali", "pin": "1234"}, self.tk)
        self.assertEqual(self.c.get("/api/admin/accounts").status_code, 401)
        t = self.post("/api/admin/login", {"pass": "adm"}).json["token"]; H = {"Authorization": "Bearer " + t}
        self.assertEqual(self.post("/api/admin/cmd", {"op": "add", "acc": 1, "val": 900}, H).json["balance"], 1000)
        self.assertEqual(self.c.get("/api/admin/accounts", headers=H).json[0]["balance"], 1000)
        u = self.post("/api/login", {"acc": 1, "pin": "1234"}).json["token"]
        self.assertEqual(self.post("/api/admin/cmd", {"op": "reset"}, {"Authorization": "Bearer " + u}).status_code, 401)  # توکن کاربر ادمین نیست

if __name__ == "__main__": unittest.main(verbosity=1)
