"""Настройки, които потребителят сменя от страницата „Настройки“ (пазят се в базата).

Всяка настройка има ключ, стойност по подразбиране и етикет в DEFAULTS.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from db import get_db

bp = Blueprint("settings", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# ключ -> (стойност по подразбиране, етикет, пояснение)
DEFAULTS = {
    "stock_value_threshold": ("20", "Праг на стойността в склада (лв)",
                              "Инвентарът в складовете се води в две групи: под прага и над прага."),
}


def init_db():
    get_db().executescript(SCHEMA)


def get_setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS[key][0]


def stock_threshold():
    """Прагът като число (20, 25.5 …) за показване в етикетите „под/над … лв“."""
    value = float(get_setting("stock_value_threshold"))
    return int(value) if value.is_integer() else value


@bp.route("/nastroiki", methods=["GET", "POST"])
def page():
    if request.method == "POST":
        raw = request.form.get("stock_value_threshold", "").strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            value = -1
        if value <= 0:
            flash("Прагът трябва да е положително число.", "bad")
        else:
            db = get_db()
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       ("stock_value_threshold", str(value)))
            db.commit()
            flash("Настройките са записани.", "ok")
        return redirect(url_for("settings.page"))
    return render_template("pages/settings.html", threshold=stock_threshold(), defaults=DEFAULTS)
