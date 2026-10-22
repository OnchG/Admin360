"""Авариен резерв: какво и колко се съхранява за аварии, по описа на язовирния район.

Редът от описа има един или няколко номера. Номерата 9-… са инвентарни номера от склада и по тях
резервът се сверява с наличното на склад (под и над прага заедно, без зачисленото по картони).
Останалите номера (SAP/Ajur, ЛПС 09-…) се водят по други списъци и още не се свързват.
"""
import json
import os
import re
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from db import get_db
from stock import SEED_DIR, fmt_qty, parse_qty, warehouses

bp = Blueprint("reserve", __name__, url_prefix="/sklad/avariyen-rezerv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reserve_items (
    id INTEGER PRIMARY KEY,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    position INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    numbers TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'бр.',
    quantity REAL NOT NULL DEFAULT 0,
    value REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Опис „Авариен резерв ЯР ВЪЧА“ – в склад Кричим.
SEED_FILE = "avariyen_rezerv_vacha.json"
SEED_WAREHOUSE = 1

STOCK_NUMBER = re.compile(r"^9-\d+$")
# Мерки, които в описа и в склада са записани различно: (в описа, в склада) -> множител.
UNIT_FACTORS = {("t", "кг"): 1000}

STATUSES = {
    "ok": "Осигурено",
    "short": "Недостиг",
    "missing": "Няма го в склада",
    "other": "Друг списък",
}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM stock_seeds WHERE filename = ?", (SEED_FILE,)).fetchone():
        with open(os.path.join(SEED_DIR, SEED_FILE), encoding="utf-8") as fh:
            for position, row in enumerate(json.load(fh), 1):
                db.execute("INSERT INTO reserve_items (warehouse_id, position, category, name, numbers, unit, quantity, value,"
                           " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (SEED_WAREHOUSE, position, row["category"], row["name"], " ".join(row["numbers"]), row["unit"],
                            row["qty"] or 0, row["value"], _now(), _now()))
        db.execute("INSERT INTO stock_seeds (filename) VALUES (?)", (SEED_FILE,))
    db.commit()


# ---------- сверка със склада ----------

def _unit(text):
    return (text or "").strip().rstrip(".").lower()


def coverage(warehouse_id):
    """Редовете от резерва със състояние спрямо наличното на склад."""
    from cards import issued_by_inv  # cards импортира stock
    db = get_db()
    rows = db.execute("SELECT * FROM reserve_items WHERE warehouse_id = ? ORDER BY position, id", (warehouse_id,)).fetchall()
    stock = defaultdict(list)
    for r in db.execute("SELECT id, inv_number, name, unit, quantity, value_class FROM stock_items WHERE warehouse_id = ?",
                        (warehouse_id,)):
        stock[r["inv_number"]].append(r)
    issued = issued_by_inv(warehouse_id)

    # Един номер може да е на няколко реда от описа (лопата права и крива): нуждата им се събира.
    items, need_by_inv = [], defaultdict(float)
    for row in rows:
        item = dict(row)
        numbers = row["numbers"].split()
        item["stock_numbers"] = [n for n in numbers if STOCK_NUMBER.match(n)]
        item["other_numbers"] = [n for n in numbers if not STOCK_NUMBER.match(n)]
        linked = [n for n in item["stock_numbers"] if n in stock]
        item["linked"] = linked
        factor = 1
        if linked:
            factor = UNIT_FACTORS.get((_unit(row["unit"]), _unit(stock[linked[0]][0]["unit"])), 1)
            for n in linked:
                need_by_inv[n] += row["quantity"] * factor / len(linked)
        item["factor"] = factor
        items.append(item)

    for item in items:
        linked = item["linked"]
        item["stock_rows"] = [r for n in linked for r in stock[n]]
        item["shared"] = [n for n in linked if need_by_inv[n] > item["quantity"] * item["factor"] / len(linked) + 1e-9]
        if linked:
            available = sum(sum(r["quantity"] for r in stock[n]) - issued.get(n, 0) for n in linked)
            need = sum(need_by_inv[n] for n in linked)
            item["available"] = available / item["factor"]
            item["need_total"] = need / item["factor"]
            item["stock_unit"] = item["stock_rows"][0]["unit"]
            item["status"] = "ok" if available + 1e-9 >= need else "short"
            item["ratio"] = min(available / need, 1) if need else 1
        elif item["stock_numbers"]:
            item["status"] = "missing"
            item["ratio"] = 0
        else:
            item["status"] = "other"
            item["ratio"] = None
        item["qty_text"] = fmt_qty(item["quantity"])
        if linked:
            item["available_text"] = fmt_qty(round(item["available"], 3))
            item["need_text"] = fmt_qty(round(item["need_total"], 3))
            item["short_text"] = fmt_qty(round(item["need_total"] - item["available"], 3))
    return items


# ---------- страници ----------

def _warehouse_id():
    ids = [w["id"] for w in warehouses()]
    wid = request.args.get("sklad", type=int)
    return wid if wid in ids else SEED_WAREHOUSE


@bp.route("/")
def index():
    warehouse_id = _warehouse_id()
    items = coverage(warehouse_id)
    status = request.args.get("sastoyanie") if request.args.get("sastoyanie") in STATUSES else ""
    query = request.args.get("q", "").strip()
    counts = {key: sum(1 for i in items if i["status"] == key) for key in STATUSES}
    shown = items
    if status:
        shown = [i for i in shown if i["status"] == status]
    if query:
        needle = query.casefold()
        shown = [i for i in shown if needle in (i["category"] + " " + i["name"] + " " + i["numbers"]).casefold()]
    groups = []
    for item in shown:
        if not groups or groups[-1]["category"] != item["category"]:
            groups.append({"category": item["category"], "items": []})
        groups[-1]["items"].append(item)
    reserve_counts = {r["warehouse_id"]: r["n"] for r in get_db().execute(
        "SELECT warehouse_id, COUNT(*) AS n FROM reserve_items GROUP BY warehouse_id")}
    return render_template(
        "pages/reserve.html", groups=groups, items=items, shown=len(shown), counts=counts, statuses=STATUSES,
        status=status, query=query, warehouses=warehouses(), warehouse_id=warehouse_id, reserve_counts=reserve_counts,
        total_value=sum(i["value"] or 0 for i in items), categories=len({i["category"] for i in items}))


def _get(item_id):
    row = get_db().execute("SELECT * FROM reserve_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _read_form():
    form = request.form
    data = {"warehouse_id": form.get("warehouse_id", type=int) or SEED_WAREHOUSE,
            "category": " ".join(form.get("category", "").split()),
            "name": " ".join(form.get("name", "").split()),
            "numbers": " ".join(form.get("numbers", "").replace(",", " ").split()),
            "unit": form.get("unit", "").strip() or "бр.",
            "note": form.get("note", "").strip()}
    errors = []
    if not data["category"]:
        errors.append("Попълнете „Вид“.")
    data["name"] = data["name"] or data["category"]
    for key, label in (("quantity", "Количество"), ("value", "Стойност")):
        try:
            data[key] = parse_qty(form.get(key))
        except ValueError:
            data[key] = None
            errors.append(f"„{label}“ трябва да е число.")
    data["quantity"] = data["quantity"] or 0
    return data, errors


FIELDS = ("warehouse_id", "category", "name", "numbers", "unit", "quantity", "value", "note")


@bp.route("/nova", methods=["GET", "POST"])
def new():
    item = {"warehouse_id": _warehouse_id(), "unit": "бр.", "category": request.args.get("vid", "")}
    if request.method == "POST":
        item, errors = _read_form()
        if not errors:
            db = get_db()
            # нов ред отива след последния от същия вид, иначе накрая
            after = db.execute("SELECT MAX(position) FROM reserve_items WHERE warehouse_id = ? AND category = ?",
                               (item["warehouse_id"], item["category"])).fetchone()[0]
            if after is None:
                after = db.execute("SELECT COALESCE(MAX(position), 0) FROM reserve_items").fetchone()[0]
            db.execute("UPDATE reserve_items SET position = position + 1 WHERE warehouse_id = ? AND position > ?",
                       (item["warehouse_id"], after))
            db.execute(f"INSERT INTO reserve_items ({', '.join(FIELDS)}, position, created_at, updated_at)"
                       f" VALUES ({', '.join('?' * len(FIELDS))}, ?, ?, ?)",
                       (*[item[f] for f in FIELDS], after + 1, _now(), _now()))
            db.commit()
            flash(f"„{item['name']}“ е добавено в аварийния резерв.", "ok")
            return redirect(url_for("reserve.index", sklad=item["warehouse_id"]))
        for e in errors:
            flash(e, "bad")
    return render_template("pages/reserve_form.html", item=item, warehouses=warehouses(), fmt_qty=fmt_qty)


@bp.route("/<int:item_id>", methods=["GET", "POST"])
def edit(item_id):
    item = dict(_get(item_id))
    if request.method == "POST":
        data, errors = _read_form()
        if not errors:
            db = get_db()
            db.execute(f"UPDATE reserve_items SET {', '.join(f + ' = ?' for f in FIELDS)}, updated_at = ? WHERE id = ?",
                       (*[data[f] for f in FIELDS], _now(), item_id))
            db.commit()
            flash(f"„{data['name']}“ е записано.", "ok")
            return redirect(url_for("reserve.index", sklad=data["warehouse_id"]))
        for e in errors:
            flash(e, "bad")
        item.update(data)
    return render_template("pages/reserve_form.html", item=item, warehouses=warehouses(), fmt_qty=fmt_qty)


@bp.route("/<int:item_id>/iztrivane", methods=["POST"])
def delete(item_id):
    row = _get(item_id)
    db = get_db()
    db.execute("DELETE FROM reserve_items WHERE id = ?", (item_id,))
    db.commit()
    flash(f"„{row['name']}“ е изтрито от аварийния резерв.", "ok")
    return redirect(url_for("reserve.index", sklad=row["warehouse_id"]))
