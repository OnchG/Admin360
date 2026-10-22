"""Admin360: деловодство, счетоводство, лични картони, склад и инвентар.
Засега само общата рамка (ляво меню, header, footer, тъмна тема) и примерни страници.

Стартиране:  py app.py   →  http://127.0.0.1:5000
"""
from datetime import date

from flask import Flask, abort, flash, redirect, render_template, request, url_for

import config
import db
import personnel
import cards
import settings
import reserve
import stock

app = Flask(__name__)
app.config["SECRET_KEY"] = "smeni-tozi-kluch"  # сменете с произволен низ преди реална употреба
db.init_app(app)
app.register_blueprint(personnel.bp)
app.register_blueprint(settings.bp)
app.register_blueprint(stock.bp)
app.register_blueprint(cards.bp)
app.register_blueprint(reserve.bp)
with app.app_context():
    personnel.init_db()
    settings.init_db()
    stock.init_db()
    cards.init_db()
    reserve.init_db()

# Страници, които още нямат съдържание: виж PLACEHOLDERS в config.py.
PLACEHOLDERS = config.PLACEHOLDERS

# Примерни данни за таблицата; заменете с база данни или файлове.
SAMPLE_ROWS = [
    {"code": "СК-001", "name": "Тонер за принтер HP 59A", "date": date(2026, 10, 20), "value": 12, "status": "ok"},
    {"code": "СК-002", "name": "Хартия А4, 500 листа", "date": date(2026, 10, 18), "value": 3, "status": "warn"},
    {"code": "СК-003", "name": "Работни ръкавици", "date": date(2026, 10, 12), "value": 0, "status": "bad"},
]
STATUS_LABELS = {"ok": "Налично", "warn": "Под минимума", "bad": "Изчерпано"}


@app.template_filter("bgdate")
def bgdate(value):
    """Дата във формат дд.мм.гггг."""
    return value.strftime("%d.%m.%Y") if value else ""


@app.template_filter("isodate")
def isodate(value):
    """„2026-10-22“ -> 22.10.2026."""
    return bgdate(date.fromisoformat(value)) if value else ""


@app.template_filter("bgnum")
def bgnum(value, digits=2):
    """Число с десетична запетая."""
    return f"{value:.{digits}f}".replace(".", ",")


@app.context_processor
def shell_context():
    return {
        "app_name": config.APP_NAME,
        "app_tagline": config.APP_TAGLINE,
        "app_author": config.APP_AUTHOR,
        "app_year": date.today().year,
        "today": date.today().isoformat(),
        "current_user": config.CURRENT_USER,
        "menu": config.MENU,
        "menu_url": menu_url,
        "status_labels": STATUS_LABELS,
    }


def menu_url(item):
    if item.get("url"):
        return item["url"]
    return url_for(item["endpoint"], **item.get("args", {}))


@app.route("/")
def dashboard():
    return render_template("pages/dashboard.html", rows=SAMPLE_ROWS)


@app.route("/sklad/nalichnosti")
def example_list():
    return render_template("pages/list.html", rows=SAMPLE_ROWS)


@app.route("/sklad/nova-pozicia", methods=["GET", "POST"])
def example_form():
    if request.method == "POST":
        flash("Позицията е записана (примерно: данните още не се пазят).", "ok")
        return redirect(url_for("example_form"))
    return render_template("pages/form.html")


@app.route("/stranica/<slug>")
def placeholder(slug):
    if slug not in PLACEHOLDERS:
        abort(404)
    return render_template("pages/placeholder.html", title=PLACEHOLDERS[slug], active=slug)


@app.route("/profil")
def profile():
    return render_template("pages/placeholder.html", title="Моят профил", active="")


@app.errorhandler(404)
def not_found(_error):
    return render_template("pages/placeholder.html", title="Страницата не е намерена", active="", missing=True), 404


if __name__ == "__main__":
    app.run(debug=True)
