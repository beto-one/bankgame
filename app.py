"""سرور بانک بازی (Flask) - مناسب PythonAnywhere. تنظیمات محرمانه از متغیرهای محیطی:
   TERMINAL_KEY  کلید دستگاه کارت‌خوان    ADMIN_PASS  رمز ادمین"""
import hmac, os
from flask import Flask, g, jsonify, request, send_from_directory
import bank

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)
app.config["DB_PATH"] = os.environ.get("BANK_DB", os.path.join(HERE, "bank.db"))
app.config["MAX_CONTENT_LENGTH"] = 4096

def db():
    if "db" not in g: g.db = bank.connect(app.config["DB_PATH"])
    return g.db

@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d: d.close()

@app.errorhandler(bank.BankError)
def _err(e): return jsonify(error=e.code), e.status

@app.errorhandler(413)
def _big(_): return jsonify(error="too_large"), 413

def body():
    j = request.get_json(silent=True)
    return j if isinstance(j, dict) else {}

def need_terminal():
    key = os.environ.get("TERMINAL_KEY", "")
    if not key: raise bank.BankError("not_configured", 503)
    if not hmac.compare_digest(request.headers.get("X-Terminal-Key", "").encode(), key.encode()):
        raise bank.BankError("terminal_auth", 403)

def bearer(): return request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

def need_admin(): bank.admin_check(db(), bearer())

@app.get("/")
def index(): return send_from_directory(HERE, "index.html")

@app.get("/api/board")
def board(): return jsonify(bank.board(db()))

# --- کارت‌خوان ---
@app.post("/api/tap")
def tap():
    need_terminal(); return jsonify(bank.tap(db(), body().get("uid")))

@app.post("/api/register")
def register():
    need_terminal(); b = body()
    return jsonify(acc=bank.register(db(), b.get("uid"), b.get("name"), b.get("pin")))

# --- کاربر ---
@app.post("/api/login")
def login():
    b = body()
    if b.get("uid") is not None: need_terminal()          # ورود با کارت فقط از ترمینال
    return jsonify(token=bank.login(db(), b.get("pin"), acc=b.get("acc"), uid=b.get("uid")))

@app.get("/api/me")
def me(): return jsonify(bank.me(db(), bearer()))

@app.post("/api/transfer")
def transfer():
    b = body(); return jsonify(bank.transfer(db(), bearer(), b.get("to"), b.get("amount"), b.get("pin")))

@app.post("/api/logout")
def logout(): bank.logout(db(), bearer()); return jsonify(ok=True)

# --- ادمین ---
@app.post("/api/admin/login")
def admin_login():
    return jsonify(token=bank.admin_login(db(), body().get("pass"), os.environ.get("ADMIN_PASS", "")))

@app.get("/api/admin/accounts")
def admin_accounts(): need_admin(); return jsonify(bank.admin_accounts(db()))

@app.post("/api/admin/cmd")
def admin_cmd():
    need_admin(); b = body()
    return jsonify(bank.admin_cmd(db(), b.get("op"), b.get("acc"), b.get("val")))
