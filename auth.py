"""Вход в системата: потребители, роли и смяна на парола.

Всяка страница изисква вход. Докато няма нито един потребител, системата води към „Първоначална настройка“,
където се създава администраторът (профилът на Онник Гюлян). Администраторът управлява потребителите
и настройките; потребителят с роля „Потребител“ работи с всички модули без тях.
Паролите се пазят само като хеш (werkzeug.security).
"""
import functools
from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db
from personnel import full_name

bp = Blueprint("auth", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    employee_id INTEGER REFERENCES employees(id),
    active INTEGER NOT NULL DEFAULT 1,
    last_login TEXT,
    created_at TEXT NOT NULL
);
"""

ROLES = {"admin": "Администратор", "user": "Потребител"}
MIN_PASSWORD = 8
# Профилът по подразбиране при първоначалната настройка.
OWNER_USERNAME = "onnik"
OWNER_NAME = ("Онник", "Гюлян")

# Страници без вход и страници само за администратор.
PUBLIC_ENDPOINTS = {"static", "auth.login", "auth.setup"}
ADMIN_ENDPOINTS = {"settings.page", "personnel.manage", "personnel.create", "personnel.edit", "personnel.remove",
                   "personnel.add_period", "personnel.remove_period",
                   "personnel.restore"}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    get_db().executescript(SCHEMA)
    get_db().commit()


def init_app(app):
    app.register_blueprint(bp)
    app.before_request(_require_login)
    app.context_processor(lambda: {"current_user": _current_user_view(), "roles": ROLES})


# ---------- текущ потребител ----------

def _load_user():
    user_id = session.get("user_id")
    row = get_db().execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone() if user_id else None
    g.user = row
    return row


def _require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if not get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("auth.setup"))
    if _load_user() is None:
        session.clear()
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    if request.endpoint in ADMIN_ENDPOINTS and g.user["role"] != "admin":
        abort(403)
    return None


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None or g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _employee(employee_id):
    return get_db().execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone() if employee_id else None


def _current_user_view():
    user = g.get("user")
    if user is None:
        return {"name": "", "position": "", "photo_url": url_for("static", filename="img/default_user.svg"), "is_admin": False}
    employee = _employee(user["employee_id"])
    photo = (url_for("personnel.photo", employee_id=employee["id"]) if employee and employee["photo"]
             else url_for("static", filename="img/default_user.svg"))
    name = f"{employee['first_name']} {employee['last_name']}" if employee else user["username"]
    return {"name": name, "position": ROLES[user["role"]], "photo_url": photo, "is_admin": user["role"] == "admin",
            "username": user["username"]}


# ---------- вход и изход ----------

def _safe_next(value):
    return value if value and value.startswith("/") and not value.startswith("//") else url_for("dashboard")


@bp.route("/vhod", methods=["GET", "POST"])
def login():
    db = get_db()
    if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("auth.setup"))
    username = request.form.get("username", "").strip()
    if request.method == "POST":
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row and row["active"] and check_password_hash(row["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = row["id"]
            session.permanent = bool(request.form.get("remember"))
            db.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), row["id"]))
            db.commit()
            return redirect(_safe_next(request.args.get("next")))
        flash("Грешно потребителско име или парола.", "bad")
    return render_template("pages/login.html", username=username, setup=False)


@bp.route("/izhod", methods=["POST"])
def logout():
    session.clear()
    flash("Излязохте от системата.", "ok")
    return redirect(url_for("auth.login"))


def _password_errors(password, repeat):
    if len(password) < MIN_PASSWORD:
        return f"Паролата трябва да е поне {MIN_PASSWORD} знака."
    if password != repeat:
        return "Двете пароли не съвпадат."
    return None


@bp.route("/nastroika", methods=["GET", "POST"])
def setup():
    """Първоначална настройка: създава администратора, докато няма нито един потребител."""
    db = get_db()
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("auth.login"))
    owner = db.execute("SELECT * FROM employees WHERE first_name = ? AND last_name = ? AND left_on IS NULL",
                       OWNER_NAME).fetchone()
    username = request.form.get("username", OWNER_USERNAME).strip()
    if request.method == "POST":
        error = _password_errors(request.form.get("password", ""), request.form.get("password2", ""))
        if not username:
            error = "Попълнете потребителско име."
        if error:
            flash(error, "bad")
        else:
            cur = db.execute("INSERT INTO users (username, password_hash, role, employee_id, created_at) VALUES (?, ?, 'admin', ?, ?)",
                             (username, generate_password_hash(request.form["password"]), owner["id"] if owner else None, _now()))
            db.commit()
            session.clear()
            session["user_id"] = cur.lastrowid
            flash("Администраторският профил е създаден. Добре дошли!", "ok")
            return redirect(url_for("dashboard"))
    return render_template("pages/login.html", username=username, setup=True,
                           owner_name=full_name(owner) if owner else None)


# ---------- моят профил ----------

@bp.route("/profil", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        if not check_password_hash(g.user["password_hash"], request.form.get("current", "")):
            flash("Текущата парола е грешна.", "bad")
        else:
            error = _password_errors(request.form.get("password", ""), request.form.get("password2", ""))
            if error:
                flash(error, "bad")
            else:
                db = get_db()
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (generate_password_hash(request.form["password"]), g.user["id"]))
                db.commit()
                flash("Паролата е сменена.", "ok")
                return redirect(url_for("auth.profile"))
    return render_template("pages/profile.html", user=g.user, employee=_employee(g.user["employee_id"]))


# ---------- потребители (администратор) ----------

@bp.route("/admin")
@admin_required
def admin_panel():
    db = get_db()
    counts = {
        "employees": db.execute("SELECT COUNT(*) FROM employees WHERE left_on IS NULL").fetchone()[0],
        "former": db.execute("SELECT COUNT(*) FROM employees WHERE left_on IS NOT NULL").fetchone()[0],
        "users": db.execute("SELECT COUNT(*) FROM users WHERE active = 1").fetchone()[0],
        "no_photo": db.execute("SELECT COUNT(*) FROM employees WHERE left_on IS NULL AND photo = ''").fetchone()[0],
    }
    return render_template("pages/admin.html", counts=counts)


def _active_employees():
    rows = get_db().execute("SELECT * FROM employees WHERE left_on IS NULL").fetchall()
    return sorted(({"id": r["id"], "name": full_name(r)} for r in rows), key=lambda e: e["name"].casefold())


def _admin_count(exclude_id=None):
    return get_db().execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id IS NOT ?",
                            (exclude_id,)).fetchone()[0]


@bp.route("/potrebiteli")
@admin_required
def users():
    rows = get_db().execute(
        "SELECT u.*, e.first_name, e.middle_name, e.last_name FROM users u LEFT JOIN employees e ON e.id = u.employee_id"
        " ORDER BY u.active DESC, u.role, u.username").fetchall()
    return render_template("pages/users.html", rows=rows)


@bp.route("/potrebiteli/nov", methods=["GET", "POST"])
@admin_required
def create_user():
    user = {"username": "", "role": "user", "employee_id": None, "active": 1}
    if request.method == "POST":
        user, error = _read_user_form()
        error = error or _password_errors(request.form.get("password", ""), request.form.get("password2", ""))
        if not error and get_db().execute("SELECT 1 FROM users WHERE username = ?", (user["username"],)).fetchone():
            error = f"Потребителското име „{user['username']}“ е заето."
        if error:
            flash(error, "bad")
        else:
            db = get_db()
            db.execute("INSERT INTO users (username, password_hash, role, employee_id, active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (user["username"], generate_password_hash(request.form["password"]), user["role"], user["employee_id"],
                        user["active"], _now()))
            db.commit()
            flash(f"Потребителят „{user['username']}“ е създаден.", "ok")
            return redirect(url_for("auth.users"))
    return render_template("pages/user_form.html", user=user, employees=_active_employees())


@bp.route("/potrebiteli/<int:user_id>", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        abort(404)
    user = dict(row)
    if request.method == "POST":
        data, error = _read_user_form()
        password = request.form.get("password", "")
        if not error and password:
            error = _password_errors(password, request.form.get("password2", ""))
        if not error and get_db().execute("SELECT 1 FROM users WHERE username = ? AND id != ?",
                                          (data["username"], user_id)).fetchone():
            error = f"Потребителското име „{data['username']}“ е заето."
        if not error and (data["role"] != "admin" or not data["active"]) and row["role"] == "admin" and not _admin_count(user_id):
            error = "Това е единственият активен администратор: не може да му се отнемат правата."
        if error:
            flash(error, "bad")
            user.update(data)
        else:
            db = get_db()
            db.execute("UPDATE users SET username = ?, role = ?, employee_id = ?, active = ? WHERE id = ?",
                       (data["username"], data["role"], data["employee_id"], data["active"], user_id))
            if password:
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))
            db.commit()
            flash(f"Потребителят „{data['username']}“ е записан.", "ok")
            return redirect(url_for("auth.users"))
    return render_template("pages/user_form.html", user=user, employees=_active_employees())


def _read_user_form():
    form = request.form
    data = {"username": form.get("username", "").strip(),
            "role": form.get("role") if form.get("role") in ROLES else "user",
            "employee_id": form.get("employee_id", type=int),
            "active": 1 if form.get("active") else 0}
    error = None if data["username"] else "Попълнете потребителско име."
    return data, error
