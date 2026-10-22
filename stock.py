"""Склад: инвентарът в складовете на язовирния район (Кричим, Цанков камък).

Всяка позиция е в един склад, в група „под прага“ или „над прага“ (прагът е настройка, сега 20 лв)
и има МОЛ: материално отговорен служител от модула „Персонал“.
Позиция, която още не е получена, е с количество 0; очакваното количество се пази отделно.

Импорт: редове във вида, в който се копират от таблицата (разделител ';', а мярката и количеството
са в отделни колони, т.е. разделени с табулация):
    1;ПАВЕЛ ПАВЛОВ;9-020003;БРАДВИ<TAB>бр<TAB>9      склад; МОЛ; инв. номер; наименование; мярка; количество
    1;ПАВЕЛ ПАВЛОВ;2001 БРАДВА<TAB>бр<TAB>2          инв. номер и наименование, разделени с интервал
    1;ПАВЕЛ ПАВЛОВ;1000<TAB>бр<TAB>                  само номер без наименование: заглавие на група, пропуска се
"""
import os
import re
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import settings
from db import get_db
from personnel import full_name, normalize_name

bp = Blueprint("stock", __name__, url_prefix="/sklad")

SCHEMA = """
CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    value_class TEXT NOT NULL CHECK (value_class IN ('under', 'over')),
    inv_number TEXT NOT NULL,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'бр',
    quantity REAL NOT NULL DEFAULT 0,
    expected_quantity REAL,
    mol_employee_id INTEGER REFERENCES employees(id),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (warehouse_id, inv_number)
);
"""

# Номерът на склада е първото поле в импортираните редове.
WAREHOUSES = [(1, "Кричим"), (2, "Цанков камък")]

VALUE_CLASSES = ("under", "over")

# Началната наличност: (файл в seed/, група, още не е получена)
SEED_FILES = [
    ("krichim_pod_prag.txt", "under", False),
    ("krichim_pod_prag_ochakvani.txt", "under", True),
]
SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def class_label(value_class, threshold=None):
    threshold = settings.stock_threshold() if threshold is None else threshold
    return f"{'Под' if value_class == 'under' else 'Над'} {threshold} лв"


def fmt_qty(value):
    """12 -> „12“, 48.7 -> „48,7“."""
    if not isinstance(value, (int, float)):
        return ""
    return f"{value:g}".replace(".", ",") if value != int(value) else str(int(value))


# ---------- база ----------

def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    db.executemany("INSERT OR IGNORE INTO warehouses (id, name) VALUES (?, ?)", WAREHOUSES)
    if db.execute("SELECT COUNT(*) FROM stock_items").fetchone()[0] == 0:
        for filename, value_class, pending in SEED_FILES:
            with open(os.path.join(SEED_DIR, filename), encoding="utf-8") as fh:
                import_lines(fh.read(), value_class, pending)
    db.commit()


def warehouses():
    return get_db().execute("SELECT * FROM warehouses ORDER BY id").fetchall()


def active_employees():
    rows = get_db().execute("SELECT * FROM employees WHERE left_on IS NULL").fetchall()
    return sorted(({"id": r["id"], "full_name": full_name(r)} for r in rows), key=lambda e: e["full_name"].casefold())


def find_employee(name):
    """МОЛ по име от импорта: „ПАВЕЛ ПАВЛОВ“ (име и фамилия) или с презиме. Връща (id, грешка)."""
    parts = normalize_name(name).split()
    if len(parts) < 2:
        return None, f"МОЛ „{name}“: нужни са поне име и фамилия"
    rows = get_db().execute(
        "SELECT * FROM employees WHERE left_on IS NULL AND lower(first_name) = lower(?) AND lower(last_name) = lower(?)",
        (parts[0], parts[-1]),
    ).fetchall()
    if len(parts) > 2:
        rows = [r for r in rows if r["middle_name"].casefold() == " ".join(parts[1:-1]).casefold()]
    if len(rows) == 1:
        return rows[0]["id"], None
    if not rows:
        return None, f"МОЛ „{name}“ не е открит сред служителите"
    return None, f"МОЛ „{name}“: има {len(rows)} служители с това име, посочете и презимето"


# ---------- импорт ----------

def parse_qty(text):
    text = (text or "").strip().replace(" ", "").replace(",", ".")
    return float(text) if text else None


def parse_line(line):
    """Един ред от импорта -> речник, None (заглавие на група) или ValueError."""
    parts = [p.strip() for p in line.replace("\t", ";").split(";")]
    if len(parts) < 4:
        raise ValueError("очакват се поне склад; МОЛ; инв. номер; мярка")
    try:
        warehouse_id = int(parts[0])
    except ValueError:
        raise ValueError(f"номерът на склада „{parts[0]}“ не е число") from None
    try:
        quantity = parse_qty(parts[-1])
    except ValueError:
        raise ValueError(f"количеството „{parts[-1]}“ не е число") from None
    unit, front = parts[-2], parts[2:-2]
    if len(front) == 1:
        match = re.match(r"^(\S+)\s+(.+)$", front[0])
        if not match:
            return None
        front = list(match.groups())
    if len(front) != 2 or not front[0] or not front[1]:
        raise ValueError("не се разпознават инвентарен номер и наименование")
    return {"warehouse_id": warehouse_id, "mol": parts[1], "inv_number": front[0],
            "name": " ".join(front[1].split()), "unit": unit or "бр", "quantity": quantity}


def import_lines(text, value_class, pending=False):
    """Записва редовете; съществуващ инв. номер в същия склад се обновява.

    pending=True: материалите още не са получени, количество 0, посоченото число става „очаквано“.
    Връща отчет {"added", "updated", "skipped", "errors": [(№ на ред, ред, причина)]}.
    """
    db = get_db()
    known = {r["id"] for r in warehouses()}
    report = {"added": 0, "updated": 0, "skipped": 0, "errors": []}
    mol_cache = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = parse_line(line)
        except ValueError as exc:
            report["errors"].append((number, line, str(exc)))
            continue
        if item is None:
            report["skipped"] += 1
            continue
        if item["warehouse_id"] not in known:
            report["errors"].append((number, line, f"няма склад с номер {item['warehouse_id']}"))
            continue
        key = item["mol"].casefold()
        if key not in mol_cache:
            mol_cache[key] = find_employee(item["mol"])
        mol_id, error = mol_cache[key]
        if error:
            report["errors"].append((number, line, error))
            continue
        quantity, expected = item["quantity"] or 0, None
        if pending:
            quantity, expected = 0, item["quantity"]
        now = _now()
        existing = db.execute("SELECT id FROM stock_items WHERE warehouse_id = ? AND inv_number = ?",
                              (item["warehouse_id"], item["inv_number"])).fetchone()
        if existing:
            db.execute(
                "UPDATE stock_items SET value_class = ?, name = ?, unit = ?, quantity = ?, expected_quantity = ?,"
                " mol_employee_id = ?, updated_at = ? WHERE id = ?",
                (value_class, item["name"], item["unit"], quantity, expected, mol_id, now, existing["id"]),
            )
            report["updated"] += 1
        else:
            db.execute(
                "INSERT INTO stock_items (warehouse_id, value_class, inv_number, name, unit, quantity, expected_quantity,"
                " mol_employee_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item["warehouse_id"], value_class, item["inv_number"], item["name"], item["unit"], quantity, expected,
                 mol_id, now, now),
            )
            report["added"] += 1
    return report


# ---------- страници ----------

def _filters():
    ids = [w["id"] for w in warehouses()]
    try:
        warehouse_id = int(request.args.get("sklad", ids[0]))
    except ValueError:
        warehouse_id = ids[0]
    if warehouse_id not in ids:
        warehouse_id = ids[0]
    value_class = request.args.get("grupa") if request.args.get("grupa") in VALUE_CLASSES else "under"
    return warehouse_id, value_class, request.args.get("q", "").strip()


@bp.route("/nalichnosti")
def items():
    warehouse_id, value_class, query = _filters()
    sql = ("SELECT s.*, e.first_name, e.middle_name, e.last_name FROM stock_items s"
           " LEFT JOIN employees e ON e.id = s.mol_employee_id WHERE s.warehouse_id = ? AND s.value_class = ?")
    rows = get_db().execute(sql + " ORDER BY s.inv_number", (warehouse_id, value_class)).fetchall()
    if query:
        needle = query.casefold()
        rows = [r for r in rows if needle in r["name"].casefold() or needle in r["inv_number"].casefold()]
    counts = {(r["warehouse_id"], r["value_class"]): r["n"] for r in get_db().execute(
        "SELECT warehouse_id, value_class, COUNT(*) AS n FROM stock_items GROUP BY warehouse_id, value_class")}
    threshold = settings.stock_threshold()
    return render_template(
        "pages/stock_items.html", rows=[_view(r) for r in rows], warehouses=warehouses(), warehouse_id=warehouse_id,
        value_class=value_class, query=query, counts=counts,
        class_labels={c: class_label(c, threshold) for c in VALUE_CLASSES},
        pending_count=sum(1 for r in rows if r["expected_quantity"] is not None and not r["quantity"]),
    )


def _view(row):
    item = dict(row)
    item["mol_name"] = full_name(row) if row["first_name"] else "—"
    item["qty"] = fmt_qty(row["quantity"])
    item["expected"] = fmt_qty(row["expected_quantity"])
    item["pending"] = row["expected_quantity"] is not None and not row["quantity"]
    return item


def _get(item_id):
    row = get_db().execute("SELECT * FROM stock_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        abort(404)
    return row


FIELD_LABELS = {"warehouse_id": "Склад", "value_class": "Група", "inv_number": "Инвентарен номер", "name": "Наименование",
                "unit": "Мярка", "quantity": "Количество", "expected_quantity": "Очаквано количество",
                "mol_employee_id": "МОЛ", "note": "Бележка"}


def _read_form():
    form = request.form
    data = {"warehouse_id": form.get("warehouse_id", type=int), "value_class": form.get("value_class"),
            "inv_number": form.get("inv_number", "").strip(), "name": " ".join(form.get("name", "").split()),
            "unit": form.get("unit", "").strip() or "бр", "mol_employee_id": form.get("mol_employee_id", type=int),
            "note": form.get("note", "").strip()}
    errors = [f"Попълнете „{FIELD_LABELS[k]}“." for k in ("inv_number", "name", "mol_employee_id") if not data[k]]
    if data["value_class"] not in VALUE_CLASSES:
        errors.append("Изберете група.")
    for key in ("quantity", "expected_quantity"):
        try:
            data[key] = parse_qty(form.get(key))
        except ValueError:
            data[key] = None
            errors.append(f"„{FIELD_LABELS[key]}“ трябва да е число.")
    data["quantity"] = data["quantity"] or 0
    return data, errors


def _form_context(item):
    threshold = settings.stock_threshold()
    return {"item": item, "warehouses": warehouses(), "employees": active_employees(),
            "class_labels": {c: class_label(c, threshold) for c in VALUE_CLASSES}, "fmt_qty": fmt_qty}


def _save(data, item_id=None):
    db = get_db()
    clash = db.execute("SELECT id FROM stock_items WHERE warehouse_id = ? AND inv_number = ? AND id IS NOT ?",
                       (data["warehouse_id"], data["inv_number"], item_id)).fetchone()
    if clash:
        return f"В този склад вече има позиция с инвентарен номер {data['inv_number']}."
    cols = list(FIELD_LABELS)
    values = [data[c] for c in cols]
    if item_id:
        db.execute(f"UPDATE stock_items SET {', '.join(c + ' = ?' for c in cols)}, updated_at = ? WHERE id = ?",
                   (*values, _now(), item_id))
    else:
        db.execute(f"INSERT INTO stock_items ({', '.join(cols)}, created_at, updated_at)"
                   f" VALUES ({', '.join('?' for _ in cols)}, ?, ?)", (*values, _now(), _now()))
    db.commit()
    return None


@bp.route("/poziciya/nova", methods=["GET", "POST"])
def create():
    warehouse_id, value_class, _ = _filters()
    if request.method == "POST":
        data, errors = _read_form()
        error = None if errors else _save(data)
        if not errors and not error:
            flash(f"Позиция {data['inv_number']} е добавена.", "ok")
            return redirect(url_for("stock.items", sklad=data["warehouse_id"], grupa=data["value_class"]))
        for message in errors or [error]:
            flash(message, "bad")
        return render_template("pages/stock_item_form.html", **_form_context(data))
    return render_template("pages/stock_item_form.html",
                           **_form_context({"warehouse_id": warehouse_id, "value_class": value_class, "unit": "бр"}))


@bp.route("/poziciya/<int:item_id>", methods=["GET", "POST"])
def edit(item_id):
    row = _get(item_id)
    if request.method == "POST":
        data, errors = _read_form()
        error = None if errors else _save(data, item_id)
        if not errors and not error:
            flash("Промените са записани.", "ok")
            return redirect(url_for("stock.items", sklad=data["warehouse_id"], grupa=data["value_class"]))
        for message in errors or [error]:
            flash(message, "bad")
        return render_template("pages/stock_item_form.html", **_form_context({**dict(row), **data}))
    return render_template("pages/stock_item_form.html", **_form_context(dict(row)))


@bp.route("/import", methods=["GET", "POST"])
def import_page():
    report = None
    text = ""
    value_class = request.form.get("value_class", "under")
    pending = bool(request.form.get("pending"))
    if request.method == "POST":
        text = request.form.get("lines", "")
        if value_class not in VALUE_CLASSES:
            value_class = "under"
        report = import_lines(text, value_class, pending)
        get_db().commit()
        if report["added"] or report["updated"]:
            flash(f"Добавени {report['added']}, обновени {report['updated']} позиции.", "ok")
        if report["errors"]:
            flash(f"{len(report['errors'])} реда не са записани, вижте списъка по-долу.", "bad")
    threshold = settings.stock_threshold()
    return render_template("pages/stock_import.html", report=report, text=text, value_class=value_class,
                           pending=pending, class_labels={c: class_label(c, threshold) for c in VALUE_CLASSES})
