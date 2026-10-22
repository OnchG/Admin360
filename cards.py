"""Картони: зачислено имущество на служител (личен картон) или на съоръжение/помещение.

Картонът има три раздела, както е на хартия:
    inventory  инвентар; свързва се със склада по инвентарен номер (под и над прага са един общ списък)
    ppe        лични предпазни средства (номера 09-…); не се водят в склада
    fixed      дълготрайни активи; имат собствена номерация и не се водят в склада

Количеството в склада е счетоводното (общо). Зачисленото по картони е част от него, така че
„на склад“ = общо − зачислено по картони. Ако по картоните излиза повече от общото, това е разминаване.

Импорт: .xls картон, втори лист: ред 3 е името, от ред 7 надолу позициите
(номенклатурен №, наименование, дата, количество), „Дълготрайни активи“ и „Лични предпазни“ започват раздел.
"""
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from db import get_db
from personnel import full_name

bp = Blueprint("cards", __name__, url_prefix="/kartoni")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'facility')),
    employee_id INTEGER REFERENCES employees(id),
    title TEXT NOT NULL,
    warehouse_id INTEGER NOT NULL DEFAULT 1 REFERENCES warehouses(id),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_items (
    id INTEGER PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    section TEXT NOT NULL CHECK (section IN ('inventory', 'ppe', 'fixed')),
    inv_number TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    issued_on TEXT,
    quantity REAL NOT NULL DEFAULT 0,
    returned_on TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS card_items_inv ON card_items (inv_number);
"""

KINDS = {"personal": "Лични картони", "facility": "Картони по съоръжения"}
SECTIONS = {"inventory": "Инвентар", "ppe": "Лични предпазни средства", "fixed": "Дълготрайни активи"}

SEED_FILE = "kartoni_krichim.json"
SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM stock_seeds WHERE filename = ?", (SEED_FILE,)).fetchone():
        with open(os.path.join(SEED_DIR, SEED_FILE), encoding="utf-8") as fh:
            for card in json.load(fh):
                save_card(card["owner"], card["kind"], card["items"])
        db.execute("INSERT INTO stock_seeds (filename) VALUES (?)", (SEED_FILE,))
    db.commit()


# ---------- връзка със склада ----------

def issued_by_inv(warehouse_id):
    """Инв. номер -> зачислено количество по всички картони на склада (невърнатото)."""
    rows = get_db().execute(
        "SELECT ci.inv_number, SUM(ci.quantity) AS q FROM card_items ci JOIN cards c ON c.id = ci.card_id"
        " WHERE c.warehouse_id = ? AND ci.section = 'inventory' AND ci.returned_on IS NULL AND ci.inv_number != ''"
        " GROUP BY ci.inv_number", (warehouse_id,)).fetchall()
    return {r["inv_number"]: r["q"] for r in rows}


def stock_totals(warehouse_id):
    """Инв. номер -> общо количество в склада (под и над прага заедно)."""
    rows = get_db().execute("SELECT inv_number, SUM(quantity) AS q FROM stock_items WHERE warehouse_id = ? GROUP BY inv_number",
                            (warehouse_id,)).fetchall()
    return {r["inv_number"]: r["q"] for r in rows}


def allocate(stock_rows, issued):
    """Разпределя зачисленото по инв. номер между позициите в склада с този номер.

    Един номер може да е и под, и над прага. Зачисленото се покрива първо от позициите извън аварийния
    резерв, после от групата под прага. Връща id на позиция -> зачислено.
    Остатъкът, който не се побира, отива на последната позиция (там се вижда разминаването).
    """
    by_inv = defaultdict(list)
    for row in stock_rows:
        by_inv[row["inv_number"]].append(row)
    result = {}
    for inv, left in issued.items():
        rows = sorted(by_inv.get(inv, []),
                      key=lambda r: ("РЕЗЕРВ" in r["name"].upper(), r["value_class"] != "under", r["id"]))
        for i, row in enumerate(rows):
            take = left if i == len(rows) - 1 else min(left, row["quantity"])
            result[row["id"]] = take
            left -= take
    return result


def holders(warehouse_id, inv_number):
    """Кой държи позиции с този инв. номер: [(картон, позиция по картона)]."""
    return get_db().execute(
        "SELECT c.id AS card_id, c.title, c.kind, ci.name, ci.quantity, ci.issued_on FROM card_items ci"
        " JOIN cards c ON c.id = ci.card_id WHERE c.warehouse_id = ? AND ci.inv_number = ? AND ci.section = 'inventory'"
        " AND ci.returned_on IS NULL ORDER BY c.title", (warehouse_id, inv_number)).fetchall()


def mismatches(warehouse_id=1):
    """Инв. номера, по които картоните надвишават общото в склада, и такива, които липсват в склада."""
    totals, issued = stock_totals(warehouse_id), issued_by_inv(warehouse_id)
    over = [(inv, totals[inv], q) for inv, q in issued.items() if inv in totals and q > totals[inv] + 1e-9]
    missing = get_db().execute(
        "SELECT c.id AS card_id, c.title, ci.* FROM card_items ci JOIN cards c ON c.id = ci.card_id"
        " WHERE c.warehouse_id = ? AND ci.section = 'inventory' AND ci.returned_on IS NULL ORDER BY ci.inv_number, c.title",
        (warehouse_id,)).fetchall()
    missing = [r for r in missing if r["inv_number"] not in totals]
    return sorted(over), missing


def card_items_for_employee(employee_id):
    """Невърнатото имущество по личния картон на служителя (ползва се при напускане)."""
    return get_db().execute(
        "SELECT ci.* FROM card_items ci JOIN cards c ON c.id = ci.card_id"
        " WHERE c.employee_id = ? AND ci.returned_on IS NULL", (employee_id,)).fetchall()


# ---------- импорт ----------

def _find_employee(name):
    from stock import find_employee  # stock импортира personnel, а тук трябва след него
    return find_employee(name)


def save_card(owner, kind, items, warehouse_id=1):
    """Създава картон или заменя позициите на съществуващ със същото име. Връща (id, грешка)."""
    db = get_db()
    employee_id = None
    title = " ".join(owner.split())
    if kind == "personal":
        employee_id, error = _find_employee(title)
        if error:
            return None, error
        title = full_name(db.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone())
        existing = db.execute("SELECT id FROM cards WHERE kind = 'personal' AND employee_id = ?", (employee_id,)).fetchone()
    else:
        existing = db.execute("SELECT id FROM cards WHERE kind = ? AND lower(title) = lower(?)", (kind, title)).fetchone()
    if existing:
        card_id = existing["id"]
        db.execute("DELETE FROM card_items WHERE card_id = ?", (card_id,))
    else:
        card_id = db.execute("INSERT INTO cards (kind, employee_id, title, warehouse_id, created_at) VALUES (?, ?, ?, ?, ?)",
                             (kind, employee_id, title, warehouse_id, _now())).lastrowid
    for item in items:
        section = item["section"]
        if section == "inventory" and item["inv"].startswith("09-"):
            section = "ppe"  # 09-… е номерация на ЛПС, дори когато е записано сред инвентара
        db.execute("INSERT INTO card_items (card_id, section, inv_number, name, issued_on, quantity, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (card_id, section, item["inv"], item["name"], item["date"], item["qty"] or 0, _now()))
    return card_id, None


def parse_xls(content):
    """Втори лист на .xls картон -> (име, [позиции])."""
    import xlrd
    sheet = xlrd.open_workbook(file_contents=content).sheet_by_index(1)
    owner = next(str(v).strip() for v in sheet.row_values(2) if str(v).strip())
    section, items = "inventory", []
    for r in range(6, sheet.nrows):
        inv, name = _cell_text(sheet.cell_value(r, 0)), " ".join(str(sheet.cell_value(r, 1)).split())
        head = f"{inv} {name}".lower()
        if "дълготрайни" in head:
            section = "fixed"
            continue
        if "предпазни" in head and not inv:
            section = "ppe"
            continue
        if not (inv or name):
            continue
        qty = sheet.cell_value(r, 3)
        items.append({"section": section, "inv": inv, "name": name, "date": _cell_date(sheet.cell_value(r, 2)),
                      "qty": float(qty) if qty not in ("", None) else 0})
    return " ".join(owner.split()), items


def _cell_text(value):
    return str(int(value)) if isinstance(value, float) else str(value).strip()


def _cell_date(value):
    if isinstance(value, float) and value:
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    parts = str(value).strip().split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return None


# ---------- страници ----------

def _card(card_id):
    row = get_db().execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _date(value):
    return date.fromisoformat(value) if value else None


@bp.route("/")
def index():
    kind = request.args.get("vid") if request.args.get("vid") in KINDS else "personal"
    rows = get_db().execute(
        "SELECT c.*, e.left_on,"
        " SUM(CASE WHEN ci.section = 'inventory' AND ci.returned_on IS NULL THEN 1 ELSE 0 END) AS n_inventory,"
        " SUM(CASE WHEN ci.section = 'ppe' AND ci.returned_on IS NULL THEN 1 ELSE 0 END) AS n_ppe,"
        " SUM(CASE WHEN ci.section = 'fixed' AND ci.returned_on IS NULL THEN 1 ELSE 0 END) AS n_fixed"
        " FROM cards c LEFT JOIN employees e ON e.id = c.employee_id LEFT JOIN card_items ci ON ci.card_id = c.id"
        " WHERE c.kind = ? GROUP BY c.id ORDER BY c.title", (kind,)).fetchall()
    counts = {r["kind"]: r["n"] for r in get_db().execute("SELECT kind, COUNT(*) AS n FROM cards GROUP BY kind")}
    over, missing = mismatches()
    return render_template("pages/cards.html", rows=rows, kind=kind, kinds=KINDS, counts=counts,
                           problem_count=len(over) + len(missing))


@bp.route("/<int:card_id>")
def show(card_id):
    card = _card(card_id)
    db = get_db()
    totals, issued = stock_totals(card["warehouse_id"]), issued_by_inv(card["warehouse_id"])
    stock_rows = db.execute("SELECT id, inv_number, name, value_class, quantity FROM stock_items WHERE warehouse_id = ?",
                            (card["warehouse_id"],)).fetchall()
    allocated = allocate(stock_rows, issued)
    stock_names = defaultdict(list)  # позициите в склада, от които се покрива зачисленото
    for r in stock_rows:
        if allocated.get(r["id"]):
            stock_names[r["inv_number"]].append(r)
    sections = {key: [] for key in SECTIONS}
    for row in db.execute("SELECT * FROM card_items WHERE card_id = ? ORDER BY returned_on IS NOT NULL, inv_number, id",
                          (card_id,)):
        item = dict(row)
        item["issued_on"], item["returned_on"] = _date(row["issued_on"]), _date(row["returned_on"])
        if row["section"] == "inventory":
            item["in_stock"] = row["inv_number"] in totals
            item["stock"] = stock_names.get(row["inv_number"], [])
            item["over"] = item["in_stock"] and issued.get(row["inv_number"], 0) > totals[row["inv_number"]] + 1e-9
        sections[row["section"]].append(item)
    employee = db.execute("SELECT * FROM employees WHERE id = ?", (card["employee_id"],)).fetchone() if card["employee_id"] else None
    return render_template("pages/card.html", card=card, sections=sections, section_labels=SECTIONS, kinds=KINDS,
                           employee=employee)


@bp.route("/<int:card_id>/poziciya", methods=["POST"])
def add_item(card_id):
    _card(card_id)
    form = request.form
    section = form.get("section") if form.get("section") in SECTIONS else "inventory"
    name = " ".join(form.get("name", "").split())
    try:
        qty = float(form.get("quantity", "").replace(",", ".") or 0)
    except ValueError:
        qty = -1
    if not name or qty <= 0:
        flash("Попълнете наименование и количество (положително число).", "bad")
    else:
        db = get_db()
        db.execute("INSERT INTO card_items (card_id, section, inv_number, name, issued_on, quantity, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (card_id, section, form.get("inv_number", "").strip(), name, form.get("issued_on") or date.today().isoformat(),
                    qty, _now()))
        db.commit()
        flash(f"„{name}“ е зачислено по картона.", "ok")
    return redirect(url_for("cards.show", card_id=card_id))


@bp.route("/poziciya/<int:item_id>/vrashtane", methods=["POST"])
def return_item(item_id):
    row = get_db().execute("SELECT * FROM card_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        abort(404)
    db = get_db()
    db.execute("UPDATE card_items SET returned_on = ? WHERE id = ?", (request.form.get("returned_on") or date.today().isoformat(), item_id))
    db.commit()
    flash(f"„{row['name']}“ е върнато в склада.", "ok")
    return redirect(url_for("cards.show", card_id=row["card_id"]))


@bp.route("/poziciya/<int:item_id>/iztrivane", methods=["POST"])
def delete_item(item_id):
    row = get_db().execute("SELECT * FROM card_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        abort(404)
    db = get_db()
    db.execute("DELETE FROM card_items WHERE id = ?", (item_id,))
    db.commit()
    flash(f"„{row['name']}“ е изтрито от картона.", "ok")
    return redirect(url_for("cards.show", card_id=row["card_id"]))


@bp.route("/import", methods=["GET", "POST"])
def import_page():
    results = []
    kind = request.form.get("kind") if request.form.get("kind") in KINDS else "personal"
    if request.method == "POST":
        for upload in request.files.getlist("files"):
            if not upload.filename:
                continue
            try:
                owner, items = parse_xls(upload.read())
            except Exception:  # повреден файл или друг формат
                results.append((upload.filename, None, "не може да се прочете като .xls картон (втори лист)"))
                continue
            card_id, error = save_card(owner, kind, items)
            results.append((upload.filename, card_id, error or f"{owner}: {len(items)} позиции"))
        get_db().commit()
    return render_template("pages/cards_import.html", results=results, kind=kind, kinds=KINDS)


@bp.route("/razminavania")
def problems():
    over, missing = mismatches()
    db = get_db()
    names = {r["inv_number"]: r["name"] for r in db.execute("SELECT inv_number, name FROM stock_items WHERE warehouse_id = 1")}
    over_rows = [{"inv": inv, "name": names.get(inv, ""), "total": total, "issued": q, "holders": holders(1, inv)}
                 for inv, total, q in over]
    return render_template("pages/cards_problems.html", over=over_rows, missing=missing)
