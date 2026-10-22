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
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from db import get_db
from personnel import full_name

bp = Blueprint("cards", __name__, url_prefix="/kartoni")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'facility', 'vehicle')),
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
    warehouse_id INTEGER REFERENCES warehouses(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS card_items_inv ON card_items (inv_number);
"""

KINDS = {"personal": "Лични картони", "facility": "Картони по съоръжения", "vehicle": "Автотранспорт"}
SECTIONS = {"inventory": "Инвентар", "ppe": "Лични предпазни средства", "fixed": "Дълготрайни активи"}

SEED_FILE = "kartoni_krichim.json"
# Картони по съоръжения, подадени след личните (зареждат се веднъж, като личните).
SEED_FACILITIES = "kartoni_saorazhenia.json"
# Корекции по първоначалния импорт (указания на потребителя от 22.10.2026), за бази, заредени преди тях.
SEED_FIX = "kartoni_krichim_fix1"
# Стари или бракувани номера, които не се зачисляват по картони.
IGNORED_INV = {"9-180049", "9-130107", "9-180019", "9-310015", "9-110075"}
# Взетото „на ЦК“ от Димитър Костадинов се води в склад Цанков камък.
TSANKOV_KAMAK = 2
DIMITAR_TSK_INV = ("9-160109", "9-050023", "9-050036", "9-170041")
NOT_DOCUMENTED = ("Компютър: ThinkCentre neo 50 s", "Клавиатура: Lenovo SK 8823", "Мишка: Lenovo Wireless L300")
# Служителите с картони от първия импорт са на яз. стена Въча (указание от 22.10.2026).
SEED_SITE_VACHA = "sluzhiteli_myasto_vacha"
SITE_VACHA = "Яз. стена Въча"
# Следващи картони: служителите от яз. стена Кричим (с поле "site") и автотранспортът.
LATER_SEEDS = ["kartoni_stena_krichim.json", "kartoni_avtotransport.json"]
# Склад на позиция по картон: собственият ѝ, ако е зададен, иначе складът на картона.
EFF = "COALESCE(ci.warehouse_id, c.warehouse_id)"
SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _allow_vehicle_kind(db):
    """Стари бази: таблицата cards е без вида 'vehicle' в CHECK. SQLite не сменя CHECK, затова се пресъздава."""
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cards'").fetchone()
    if not sql or "'vehicle'" in sql[0]:
        return
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")  # иначе DROP TABLE изтрива позициите по картоните (ON DELETE CASCADE)
    try:
        db.executescript(
            # нова таблица, копие и смяна на името (не RENAME на старата: тогава card_items сочи към нея)
            "BEGIN;"
            + SCHEMA.split("CREATE TABLE IF NOT EXISTS card_items")[0].replace("IF NOT EXISTS cards (", "cards_new (") +
            "INSERT INTO cards_new SELECT * FROM cards;"
            "DROP TABLE cards;"
            "ALTER TABLE cards_new RENAME TO cards;"
            "COMMIT;")
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def _load_seed(db, filename):
    if db.execute("SELECT 1 FROM stock_seeds WHERE filename = ?", (filename,)).fetchone():
        return
    with open(os.path.join(SEED_DIR, filename), encoding="utf-8") as fh:
        for card in json.load(fh):
            card_id, _error = save_card(card["owner"], card["kind"], card["items"])
            if card_id and card.get("site"):
                db.execute("UPDATE employees SET site = ? WHERE site = '' AND id ="
                           " (SELECT employee_id FROM cards WHERE id = ?)", (card["site"], card_id))
    db.execute("INSERT INTO stock_seeds (filename) VALUES (?)", (filename,))


def init_db():
    db = get_db()
    _allow_vehicle_kind(db)
    db.executescript(SCHEMA)
    if "warehouse_id" not in {r["name"] for r in db.execute("PRAGMA table_info(card_items)")}:
        db.execute("ALTER TABLE card_items ADD COLUMN warehouse_id INTEGER REFERENCES warehouses(id)")
    if not db.execute("SELECT 1 FROM stock_seeds WHERE filename = ?", (SEED_FILE,)).fetchone():
        with open(os.path.join(SEED_DIR, SEED_FILE), encoding="utf-8") as fh:
            for card in json.load(fh):
                save_card(card["owner"], card["kind"], card["items"])
        db.execute("INSERT INTO stock_seeds (filename) VALUES (?)", (SEED_FILE,))
    if not db.execute("SELECT 1 FROM stock_seeds WHERE filename = ?", (SEED_FACILITIES,)).fetchone():
        with open(os.path.join(SEED_DIR, SEED_FACILITIES), encoding="utf-8") as fh:
            for card in json.load(fh):
                save_card(card["owner"], card["kind"], card["items"])
        db.execute("INSERT INTO stock_seeds (filename) VALUES (?)", (SEED_FACILITIES,))
    if not db.execute("SELECT 1 FROM stock_seeds WHERE filename = ?", (SEED_FIX,)).fetchone():
        marks = ",".join("?" * len(IGNORED_INV))
        db.execute(f"DELETE FROM card_items WHERE section = 'inventory' AND inv_number IN ({marks})", tuple(IGNORED_INV))
        marks = ",".join("?" * len(NOT_DOCUMENTED))
        db.execute(f"DELETE FROM card_items WHERE inv_number = '' AND name IN ({marks})", NOT_DOCUMENTED)
        marks = ",".join("?" * len(DIMITAR_TSK_INV))
        db.execute(f"UPDATE card_items SET warehouse_id = ? WHERE inv_number IN ({marks}) AND card_id IN"
                   " (SELECT c.id FROM cards c JOIN employees e ON e.id = c.employee_id"
                   "  WHERE e.first_name = 'Димитър' AND e.last_name = 'Костадинов')",
                   (TSANKOV_KAMAK, *DIMITAR_TSK_INV))
        db.execute("INSERT INTO stock_seeds (filename) VALUES (?)", (SEED_FIX,))
    if not db.execute("SELECT 1 FROM stock_seeds WHERE filename = ?", (SEED_SITE_VACHA,)).fetchone():
        db.execute("UPDATE employees SET site = ? WHERE site = '' AND id IN"
                   " (SELECT employee_id FROM cards WHERE kind = 'personal')", (SITE_VACHA,))
        db.execute("INSERT INTO stock_seeds (filename) VALUES (?)", (SEED_SITE_VACHA,))
    for filename in LATER_SEEDS:
        _load_seed(db, filename)
    db.commit()


# ---------- връзка със склада ----------

def issued_by_inv(warehouse_id):
    """Инв. номер -> зачислено количество по всички картони на склада (невърнатото)."""
    rows = get_db().execute(
        "SELECT ci.inv_number, SUM(ci.quantity) AS q FROM card_items ci JOIN cards c ON c.id = ci.card_id"
        f" WHERE {EFF} = ? AND ci.section = 'inventory' AND ci.returned_on IS NULL AND ci.inv_number != ''"
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
        f" JOIN cards c ON c.id = ci.card_id WHERE {EFF} = ? AND ci.inv_number = ? AND ci.section = 'inventory'"
        " AND ci.returned_on IS NULL ORDER BY c.title", (warehouse_id, inv_number)).fetchall()


def holders_by_inv(warehouse_id):
    """Инв. номер -> кой държи позиции с него (за всички номера в склада наведнъж)."""
    result = defaultdict(list)
    for row in get_db().execute(
            "SELECT c.id AS card_id, c.title, c.kind, ci.inv_number, ci.name, ci.quantity, ci.issued_on FROM card_items ci"
            f" JOIN cards c ON c.id = ci.card_id WHERE {EFF} = ? AND ci.section = 'inventory'"
            " AND ci.returned_on IS NULL AND ci.inv_number != '' ORDER BY c.kind, c.title", (warehouse_id,)):
        result[row["inv_number"]].append(row)
    return result


def stocked_warehouses():
    """Складове, за които вече има заредени наличности."""
    return {r["warehouse_id"] for r in get_db().execute("SELECT DISTINCT warehouse_id FROM stock_items")}


def mismatches(warehouse_id=1):
    """Инв. номера, по които картоните надвишават общото в склада, и такива, които липсват в склада.

    Позиции от склад без заредени наличности не са разминаване: те се свързват, когато складът бъде зареден.
    """
    totals, issued = stock_totals(warehouse_id), issued_by_inv(warehouse_id)
    over = [(inv, totals[inv], q) for inv, q in issued.items() if inv in totals and q > totals[inv] + 1e-9]
    missing = get_db().execute(
        "SELECT c.id AS card_id, c.title, c.kind, ci.* FROM card_items ci JOIN cards c ON c.id = ci.card_id"
        f" WHERE {EFF} = ? AND ci.section = 'inventory' AND ci.returned_on IS NULL ORDER BY ci.inv_number, c.title",
        (warehouse_id,)).fetchall()
    # принадлежностите на автомобил без номер (крик, аптечка…) не се водят в склада
    missing = [r for r in missing if r["inv_number"] not in totals and (r["inv_number"] or r["kind"] != "vehicle")]
    return sorted(over), missing


def all_mismatches():
    """mismatches() за всички складове със заредени наличности."""
    over, missing = [], []
    for warehouse_id in sorted(stocked_warehouses()):
        o, m = mismatches(warehouse_id)
        over += [(warehouse_id, *row) for row in o]
        missing += m
    return over, missing


def awaiting_stock():
    """Позиции по картони, свързани със склад, чиито наличности още не са заредени."""
    stocked = stocked_warehouses()
    rows = get_db().execute(
        f"SELECT c.id AS card_id, c.title, w.id AS wid, w.name AS warehouse, ci.* FROM card_items ci"
        f" JOIN cards c ON c.id = ci.card_id JOIN warehouses w ON w.id = {EFF}"
        " WHERE ci.section = 'inventory' AND ci.returned_on IS NULL ORDER BY w.id, c.title, ci.inv_number").fetchall()
    return [r for r in rows if r["wid"] not in stocked]


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
        if item["section"] == "inventory" and item["inv"] in IGNORED_INV:
            continue
        section = item["section"]
        if section == "inventory" and item["inv"].startswith("09-"):
            section = "ppe"  # 09-… е номерация на ЛПС, дори когато е записано сред инвентара
        db.execute("INSERT INTO card_items (card_id, section, inv_number, name, issued_on, quantity, warehouse_id, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (card_id, section, item["inv"], item["name"], item["date"], item["qty"] or 0, item.get("warehouse"), _now()))
    return card_id, None


def parse_xls(content):
    """Втори лист на .xls картон -> (име, [позиции]).

    Картоните по съоръжения са на първия лист и са по-кратки: инв. номер, наименование, количество.
    """
    import xlrd
    book = xlrd.open_workbook(file_contents=content)
    if book.nsheets < 2 or not book.sheet_by_index(1).nrows:
        return _parse_short(book.sheet_by_index(0))
    sheet = book.sheet_by_index(1)
    # име на служителя; при автомобил – номер на актива и наименование („5301014 | УАЗ С 3836 ВР“): взема се текстът
    cells = [str(v).strip() for v in sheet.row_values(2) if str(v).strip()]
    owner = next((c for c in cells if not re.fullmatch(r"[\d.]+", c)), cells[0])
    section, items = "inventory", []
    for r in range(6, sheet.nrows):
        row = sheet.row_values(r) + ["", "", "", ""]
        if isinstance(row[1], float) and isinstance(row[0], str) and not re.match(r"^\s*\d", row[0]):
            row = [""] + row  # ред без номер: наименованието е в първата колона
        inv, name = _cell_text(row[0]), " ".join(str(row[1]).split())
        head = f"{inv} {name}".lower()
        if "дълготрайни" in head:
            section = "fixed"
            continue
        if "предпазни" in head and not inv:
            section = "ppe"
            continue
        if not (inv or name):
            continue
        if not name and items and inv:
            name = items[-1]["name"]  # поредни номера от един комплект: наименованието е само на първия ред
        # картон без раздел „Дълготрайни“: номерата извън 9-…/09-… пак са дълготрайни активи (напр. 206/102)
        row_section = "fixed" if section == "inventory" and inv and not re.match(r"^0?9-", inv) else section
        items.append({"section": row_section, "inv": inv, "name": name, "date": _cell_date(row[2]), "qty": _cell_qty(row[3])})
    return " ".join(owner.split()), items


def _parse_short(sheet):
    """Кратък картон: заглавие на 4-ия ред, позициите от 8-ия (инв. номер, наименование, количество)."""
    owner = next(str(v).strip() for v in sheet.row_values(3) if str(v).strip())
    items = []
    for r in range(7, sheet.nrows):
        inv, name = _cell_text(sheet.cell_value(r, 0)), " ".join(str(sheet.cell_value(r, 1)).split())
        if not name:
            continue  # празен ред или междинно заглавие
        qty = _cell_qty(sheet.cell_value(r, 2))
        # номерата извън 9-…/09-… са дълготрайни активи (собствена номерация, напр. „64 / 6“)
        section = "inventory" if re.match(r"^0?9-", inv) else "fixed"
        items.append({"section": section, "inv": inv, "name": name, "date": None,
                      "qty": qty})
    return " ".join(owner.split()), items


def _cell_qty(value):
    """Количество от клетка: число или текст като „10м“, „2,5“ (мярката се пропуска)."""
    if isinstance(value, (int, float)):
        return float(value)
    match = re.match(r"\s*(\d+(?:[.,]\d+)?)", str(value))
    return float(match.group(1).replace(",", ".")) if match else 0


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
    from personnel import SITES
    kind = request.args.get("vid") if request.args.get("vid") in KINDS else "personal"
    site = request.args.get("myasto") if request.args.get("myasto") in SITES else ""
    rows = get_db().execute(
        "SELECT c.*, e.left_on, e.site,"
        " SUM(CASE WHEN ci.section = 'inventory' AND ci.returned_on IS NULL THEN 1 ELSE 0 END) AS n_inventory,"
        " SUM(CASE WHEN ci.section = 'ppe' AND ci.returned_on IS NULL THEN 1 ELSE 0 END) AS n_ppe,"
        " SUM(CASE WHEN ci.section = 'fixed' AND ci.returned_on IS NULL THEN 1 ELSE 0 END) AS n_fixed"
        " FROM cards c LEFT JOIN employees e ON e.id = c.employee_id LEFT JOIN card_items ci ON ci.card_id = c.id"
        " WHERE c.kind = ? GROUP BY c.id ORDER BY c.title", (kind,)).fetchall()
    site_counts = {s: sum(1 for r in rows if r["site"] == s) for s in SITES}
    if kind == "personal" and site:
        rows = [r for r in rows if r["site"] == site]
    counts = {r["kind"]: r["n"] for r in get_db().execute("SELECT kind, COUNT(*) AS n FROM cards GROUP BY kind")}
    over, missing = all_mismatches()
    return render_template("pages/cards.html", rows=rows, kind=kind, kinds=KINDS, counts=counts,
                           problem_count=len(over) + len(missing), sites=SITES, site=site, site_counts=site_counts)


def _stock_link(warehouse_id):
    """Данни за свързване на позиции по картони с наличностите в един склад."""
    db = get_db()
    totals, issued = stock_totals(warehouse_id), issued_by_inv(warehouse_id)
    stock_rows = db.execute("SELECT id, inv_number, name, value_class, quantity FROM stock_items WHERE warehouse_id = ?",
                            (warehouse_id,)).fetchall()
    allocated = allocate(stock_rows, issued)
    stock_names = defaultdict(list)  # позициите в склада, от които се покрива зачисленото
    for r in stock_rows:
        if allocated.get(r["id"]):
            stock_names[r["inv_number"]].append(r)
    return {"totals": totals, "issued": issued, "names": stock_names, "loaded": bool(stock_rows)}


@bp.route("/<int:card_id>")
def show(card_id):
    card = _card(card_id)
    db = get_db()
    warehouse_names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM warehouses")}
    links = {}
    sections = {key: [] for key in SECTIONS}
    for row in db.execute("SELECT * FROM card_items WHERE card_id = ? ORDER BY returned_on IS NOT NULL, inv_number, id",
                          (card_id,)):
        item = dict(row)
        item["issued_on"], item["returned_on"] = _date(row["issued_on"]), _date(row["returned_on"])
        if row["section"] == "inventory":
            wid = row["warehouse_id"] or card["warehouse_id"]
            link = links.get(wid) or links.setdefault(wid, _stock_link(wid))
            inv = row["inv_number"]
            item["warehouse"] = wid
            item["other_warehouse"] = warehouse_names[wid] if wid != card["warehouse_id"] else None
            item["awaiting"] = not link["loaded"]
            item["in_stock"] = inv in link["totals"]
            item["stock"] = link["names"].get(inv, [])
            item["over"] = item["in_stock"] and link["issued"].get(inv, 0) > link["totals"][inv] + 1e-9
        sections[row["section"]].append(item)
    employee = db.execute("SELECT * FROM employees WHERE id = ?", (card["employee_id"],)).fetchone() if card["employee_id"] else None
    active = request.args.get("razdel") if request.args.get("razdel") in SECTIONS else "inventory"
    return render_template("pages/card.html", card=card, sections=sections, section_labels=SECTIONS, kinds=KINDS,
                           employee=employee, warehouses=warehouse_names, active=active)


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
        card = _card(card_id)
        warehouse_id = form.get("warehouse_id", type=int)
        if section != "inventory" or warehouse_id == card["warehouse_id"]:
            warehouse_id = None  # по подразбиране – складът на картона
        db.execute("INSERT INTO card_items (card_id, section, inv_number, name, issued_on, quantity, warehouse_id, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (card_id, section, form.get("inv_number", "").strip(), name, form.get("issued_on") or date.today().isoformat(),
                    qty, warehouse_id, _now()))
        db.commit()
        flash(f"„{name}“ е зачислено по картона.", "ok")
    return redirect(url_for("cards.show", card_id=card_id, razdel=section))


@bp.route("/poziciya/<int:item_id>/vrashtane", methods=["POST"])
def return_item(item_id):
    row = get_db().execute("SELECT * FROM card_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        abort(404)
    db = get_db()
    db.execute("UPDATE card_items SET returned_on = ? WHERE id = ?", (request.form.get("returned_on") or date.today().isoformat(), item_id))
    db.commit()
    flash(f"„{row['name']}“ е върнато в склада.", "ok")
    return redirect(url_for("cards.show", card_id=row["card_id"], razdel=row["section"]))


@bp.route("/poziciya/<int:item_id>/iztrivane", methods=["POST"])
def delete_item(item_id):
    row = get_db().execute("SELECT * FROM card_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        abort(404)
    db = get_db()
    db.execute("DELETE FROM card_items WHERE id = ?", (item_id,))
    db.commit()
    flash(f"„{row['name']}“ е изтрито от картона.", "ok")
    return redirect(url_for("cards.show", card_id=row["card_id"], razdel=row["section"]))


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
    over, missing = all_mismatches()
    db = get_db()
    names = {(r["warehouse_id"], r["inv_number"]): r["name"]
             for r in db.execute("SELECT warehouse_id, inv_number, name FROM stock_items")}
    over_rows = [{"inv": inv, "name": names.get((wid, inv), ""), "total": total, "issued": q, "holders": holders(wid, inv)}
                 for wid, inv, total, q in over]
    return render_template("pages/cards_problems.html", over=over_rows, missing=missing, awaiting=awaiting_stock())
