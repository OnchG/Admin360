"""Персонал: личен състав на служителите.

Служител не се изтрива: при премахване получава дата на напускане и минава в „Бивши служители“.
Всяка промяна (постъпване, редакция, напускане, връщане) се записва в employee_events.

Бъдещи лични картони: имуществото по картон ще сочи служителя с employee_id. Преди напускане
card_items_for() ще връща зачисленото му имущество и за всяка позиция ще се избира
„прехвърли на друг служител“ или „бракувай“; служителят напуска едва когато нищо не остане по картона му.
"""
import os
import re
import secrets
from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for

from db import get_db

bp = Blueprint("personnel", __name__, url_prefix="/personal")

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    middle_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL,
    position TEXT NOT NULL,
    hired_on TEXT,
    left_on TEXT,
    leave_reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employee_events (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    kind TEXT NOT NULL,
    happened_at TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT ''
);
"""

# Подредба по длъжност: първото съвпадение определя групата (регулярен израз върху длъжността).
RANK_GROUPS = [
    ("Ръководство", r"^(началник|ръководител|директор|управител)"),
    ("Инженери, ХЕС", r"^инженер.*\bхес\b"),
    ("Инженери, електрооборудване", r"^инженер.*електрооборудване"),
    ("Инженери", r"^инженер"),
    ("Техници", r"^техник"),
    ("Монтьори", r"^монтьор"),
    ("Машинисти", r"^машинист"),
    ("Работници", r"^работник"),
    ("Чистачи", r"^чистач"),
    ("Счетоводители", r"^(главен )?счетоводител"),
    ("Касиери", r"^касиер"),
]
OTHER_GROUP = "Други"
GROUP_ORDER = [label for label, _ in RANK_GROUPS] + [OTHER_GROUP]

# Съкращения в длъжностите, които остават с главни букви.
ABBREVIATIONS = {"ХЕС", "ВЕЦ", "ПАВЕЦ", "ПП", "КИП", "ПСМ", "ТЕЦ", "ЯЗ", "ГПП"}

EVENT_LABELS = {"hired": "Постъпване", "edited": "Редакция", "left": "Напускане", "restored": "Върнат на работа"}

# Началният личен състав (име, длъжност), както е подаден.
SEED = """
АЛЕКСАНДРА АЛЕКСАНДРОВА СТАМАТОВА|инженер, ХЕС
АЛЕКСАНДЪР БОРИСОВ УДОНДЖИЕВ|инженер, ХЕС
АНГЕЛ МИЛКОВ МИЛЕВ|инженер, ХЕС
АНГЕЛ МИТКОВ ТАНИН|Инженер, ХЕС
АНГЕЛ СТОЯНОВ ЧУЧУЛИГОВ|работник, поддръжка на сгради и ХЕС
АННА АТАНАСОВА ЧЕЛЕБИЕВА|Работник, ХЕС
БОГДАН РУМЕНОВ БАШЕВ|монтьор, ремонт и поддръжка на машини
БОРИС ВАСИЛЕВ КОИЛОВ|Работник, ХЕС
ВЕЛИН РУМЕНОВ АСЕНОВ|Техник, електрооборудване
ВИЛИ СТОИМЕНОВ ГЬОРДЖИЕВ|Техник, електрооборудване
ГЕОРГИ СТЕФАНОВ ПЕЙКОВ|машинист, ПСМ и поддр.маш.част на ХЕС
ДАНАИЛ ЙОРДАНОВ ЩЕРЕВ|работник, поддръжка на сгради и ХЕС
ДИМИТЪР ИЛЧЕВ КОСТАДИНОВ|Инженер, КИП и А от управлението
ДИМО ВАСИЛЕВ СЕДЯНКОВ|Работник, ХЕС
ИВЕЛИН ВЪЛЕВ ИВАНОВ|техник, геодезия
ИВАН ДИМИТРОВ МАРКОВ|Техник, хидрология и водостопански измерв.
ИСМАИЛ МЕХМЕДОВ ТАТАРОВ|Работник, ХЕС
КОСТАДИН ПАВЛОВ ПАВЛОВ|Техник, хидрология и водостопански измерв.
КРАСИМИР НИКОЛОВ НИКОЛОВ|монтьор, ремонт и поддръжка на машини
МАНОЛ БОРИСОВ ДИМИТРОВ|работник, поддръжка на сгради и ХЕС
НАСКО ЕМИЛОВ ТАРАШЕВ|работник, поддръжка на сгради и ХЕС
НИКОЛАЙ ВАЛЕНТИНОВ ВАСИЛЕВ|касиер, домакин
НИКОЛАЙ СТОЯНОВ ПАПАЛАКОВ|Техник, ремонт и поддържане на машини
ОННИК БОХОС ГЮЛЯН|инженер, ХЕС
ПАВЕЛ КОСТАДИНОВ ПАВЛОВ|касиер, домакин
ПЕТРУНКА КОСТАДИНОВА НИКОЛОВА|чистач, производствени помещения
ПЕТЪР ВАСИЛЕВ СЕРАФИМОВ|монтьор, ремонт и поддръжка на машини
ПЕТЪР КОСТАДИНОВ КОИЛОВ|Техник, електрооборудване
РАМАДАН ИДРИЗОВ АСАНОВ|работник, поддръжка на сгради и ХЕС
РУМЕН ЙОНКОВ ПАВЛОВ|Техник, ремонт и поддържане на машини
РУСИ ВАДИМОВ ЧАУШЕВ|Техник, геодезия
СЕВЕР ТАШКЪНОВ МЮМЮНОВ|монтьор, ремонт и поддръжка на машини
СПАСКА ГЕОРГИЕВА СОКЕРОВА|Работник, ХЕС
СТОЯН ПЕТКОВ ИВАНОВ|Работник, ХЕС
СТОЯН ЦВЕТАНОВ КОДЖАМАНОВ|Техник, електрооборудване
СИЛВИЯ СЕЛВИ ЧУЧУЛИГОВА|Чистач, производствени помещения
СУЗАНА МЛАДЕНОВА КЕХАЙОВА|чистач, производствени помещения
ХРИСТО ЙОНЧЕВ ПЕТРОВ|Техник, електрооборудване
ЮЛИЯН СТРАХИЛОВ ЛЮБЕНОВ|Работник, ХЕС
ЮСУФ ЮСЕИНОВ СЮЛЕЙМАНОВ|монтьор, електрооборудване
СТЕФАН ЕМИЛОВ КОЙЧЕВ|Ръководител ПП
ВЛАДО ИВАНОВ БОЯРОВ|Инженер, електрооборудване
"""


# ---------- нормализиране ----------

def normalize_name(value):
    """„ИВАН ПЕТРОВ-ГЕОРГИЕВ“ -> „Иван Петров-Георгиев“: главна първа буква на всяка част."""
    words = (value or "").split()
    return " ".join("-".join(part[:1].upper() + part[1:].lower() for part in word.split("-")) for word in words)


def normalize_position(value):
    """Главна първа буква, останалото малки; съкращенията (ХЕС, ПП, КИП и А …) остават с главни."""
    text = " ".join((value or "").split()).lower()
    text = re.sub(r"\bкип и а\b", "КИП и А", text)
    text = re.sub(r"[а-яa-z]+", lambda m: m.group(0).upper() if m.group(0).upper() in ABBREVIATIONS else m.group(0), text)
    return text[:1].upper() + text[1:]


def rank_group(position):
    text = (position or "").lower()
    for label, pattern in RANK_GROUPS:
        if re.search(pattern, text):
            return label
    return OTHER_GROUP


def full_name(row):
    return " ".join(part for part in (row["first_name"], row["middle_name"], row["last_name"]) if part)


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ---------- база ----------

def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    if "photo" not in {r["name"] for r in db.execute("PRAGMA table_info(employees)")}:
        db.execute("ALTER TABLE employees ADD COLUMN photo TEXT NOT NULL DEFAULT ''")
    if db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
        for line in SEED.strip().splitlines():
            name, position = line.split("|")
            parts = normalize_name(name).split()
            _insert(db, parts[0], " ".join(parts[1:-1]), parts[-1], normalize_position(position), None, "", "Начален списък")
    db.commit()


def _insert(db, first, middle, last, position, hired_on, note, details=""):
    now = _now()
    cur = db.execute(
        "INSERT INTO employees (first_name, middle_name, last_name, position, hired_on, note, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (first, middle, last, position, hired_on, note, now, now),
    )
    _log(db, cur.lastrowid, "hired", details)
    return cur.lastrowid


# ---------- снимки ----------

PHOTO_SIZE = 800  # по-дългата страна, в пиксели


def _photo_dir():
    path = os.path.join(current_app.instance_path, "photos")
    os.makedirs(path, exist_ok=True)
    return path


def _save_photo(db, employee_id, upload):
    """Записва качената снимка (смалена, JPEG) и връща грешка или None."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        image = ImageOps.exif_transpose(Image.open(upload.stream))
        image.thumbnail((PHOTO_SIZE, PHOTO_SIZE))
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError):
        return "Файлът не е снимка (поддържат се JPG, PNG, WEBP и др.)."
    # ново име при всяка смяна, за да не се показва старата снимка от кеша на браузъра
    filename = f"{employee_id}-{secrets.token_hex(4)}.jpg"
    image.save(os.path.join(_photo_dir(), filename), "JPEG", quality=85)
    _drop_photo(db, employee_id)
    db.execute("UPDATE employees SET photo = ? WHERE id = ?", (filename, employee_id))
    return None


def _drop_photo(db, employee_id):
    row = db.execute("SELECT photo FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if row and row["photo"]:
        try:
            os.remove(os.path.join(_photo_dir(), row["photo"]))
        except FileNotFoundError:
            pass
        db.execute("UPDATE employees SET photo = '' WHERE id = ?", (employee_id,))


def _apply_photo(db, employee_id):
    """Снимка от формата: нова, премахване или без промяна. Връща описание за историята или None."""
    upload = request.files.get("photo")
    if upload and upload.filename:
        error = _save_photo(db, employee_id, upload)
        if error:
            flash(error, "bad")
            return None
        return "Снимка: нова"
    if request.form.get("remove_photo"):
        _drop_photo(db, employee_id)
        return "Снимка: премахната"
    return None


def _log(db, employee_id, kind, details=""):
    db.execute(
        "INSERT INTO employee_events (employee_id, kind, happened_at, details) VALUES (?, ?, ?, ?)",
        (employee_id, kind, _now(), details),
    )


def _get(employee_id):
    row = get_db().execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _view(row):
    item = dict(row)
    item["full_name"] = full_name(row)
    item["initials"] = (row["first_name"][:1] + row["last_name"][:1]).upper()
    item["group"] = rank_group(row["position"])
    for key in ("hired_on", "left_on"):
        item[key] = date.fromisoformat(row[key]) if row[key] else None
    return item


def sort_key_alpha(item):
    return (item["first_name"].casefold(), item["middle_name"].casefold(), item["last_name"].casefold())


def sort_key_rank(item):
    return (GROUP_ORDER.index(item["group"]), item["position"].casefold(), *sort_key_alpha(item))


def card_items_for(employee_id):
    """Невърнатото имущество, зачислено по личния картон на служителя."""
    from cards import card_items_for_employee  # cards импортира personnel
    return card_items_for_employee(employee_id)


# ---------- страници ----------

@bp.route("/sluzhiteli")
def employees():
    sort = "az" if request.args.get("sort") == "az" else "rank"
    view = "karti" if request.args.get("izgled") == "karti" else "tablica"
    rows = [_view(r) for r in get_db().execute("SELECT * FROM employees WHERE left_on IS NULL")]
    rows.sort(key=sort_key_alpha if sort == "az" else sort_key_rank)
    former = get_db().execute("SELECT COUNT(*) FROM employees WHERE left_on IS NOT NULL").fetchone()[0]
    cards = {r["employee_id"]: r for r in get_db().execute(
        "SELECT c.employee_id, c.id, SUM(CASE WHEN ci.returned_on IS NULL AND ci.id IS NOT NULL THEN 1 ELSE 0 END) AS n"
        " FROM cards c LEFT JOIN card_items ci ON ci.card_id = c.id WHERE c.kind = 'personal' GROUP BY c.id")}
    return render_template("pages/employees.html", rows=rows, sort=sort, view=view, former_count=former, cards=cards,
                           manage=request.endpoint == "personnel.manage")


@bp.route("/upravlenie")
def manage():
    """Списъкът служители с бутоните за редакция (админ панел)."""
    return employees()


@bp.route("/sluzhiteli/<int:employee_id>")
def show(employee_id):
    """Карта на служителя: снимка, данни, имущество по картон, потребителски профил и история."""
    db = get_db()
    employee = _view(_get(employee_id))
    today = date.today()
    if employee["hired_on"]:
        end = employee["left_on"] or today
        months = (end.year - employee["hired_on"].year) * 12 + end.month - employee["hired_on"].month - (end.day < employee["hired_on"].day)
        employee["service"] = (months // 12, months % 12)
    card = db.execute("SELECT * FROM cards WHERE kind = 'personal' AND employee_id = ?", (employee_id,)).fetchone()
    items = db.execute("SELECT * FROM card_items WHERE card_id = ? AND returned_on IS NULL ORDER BY section, inv_number",
                       (card["id"],)).fetchall() if card else []
    account = db.execute("SELECT * FROM users WHERE employee_id = ?", (employee_id,)).fetchone()
    mol_count = db.execute("SELECT COUNT(*) FROM stock_items WHERE mol_employee_id = ?", (employee_id,)).fetchone()[0]
    return render_template("pages/employee_show.html", employee=employee, card=card, items=items, account=account,
                           mol_count=mol_count, events=_events(employee_id)[:10])


@bp.route("/sluzhiteli/<int:employee_id>/snimka")
def photo(employee_id):
    row = _get(employee_id)
    if not row["photo"]:
        abort(404)
    return send_from_directory(_photo_dir(), row["photo"], max_age=31536000)


@bp.route("/bivshi")
def former():
    rows = [_view(r) for r in get_db().execute("SELECT * FROM employees WHERE left_on IS NOT NULL ORDER BY left_on DESC")]
    return render_template("pages/employees_former.html", rows=rows)


@bp.route("/sluzhiteli/nov", methods=["GET", "POST"])
def create():
    if request.method == "POST":
        data, errors = _read_form()
        if not errors:
            db = get_db()
            employee_id = _insert(db, data["first_name"], data["middle_name"], data["last_name"], data["position"],
                                  data["hired_on"], data["note"])
            _apply_photo(db, employee_id)
            db.commit()
            flash(f"Служителят {data['first_name']} {data['last_name']} е добавен.", "ok")
            return redirect(url_for("personnel.manage"))
        for message in errors:
            flash(message, "bad")
        return render_template("pages/employee_form.html", employee=data, positions=_positions(), events=[])
    return render_template("pages/employee_form.html", employee={}, positions=_positions(), events=[])


@bp.route("/sluzhiteli/<int:employee_id>/redakcia", methods=["GET", "POST"])
def edit(employee_id):
    row = _get(employee_id)
    if request.method == "POST":
        data, errors = _read_form()
        if not errors:
            changes = [f"{label}: {row[key] or '—'} → {data[key] or '—'}"
                       for key, label in FIELD_LABELS.items() if (row[key] or "") != (data[key] or "")]
            db = get_db()
            photo_change = _apply_photo(db, employee_id)
            if photo_change:
                changes.append(photo_change)
            if changes:
                db.execute(
                    "UPDATE employees SET first_name = ?, middle_name = ?, last_name = ?, position = ?, hired_on = ?, note = ?,"
                    " updated_at = ? WHERE id = ?",
                    (data["first_name"], data["middle_name"], data["last_name"], data["position"], data["hired_on"],
                     data["note"], _now(), employee_id),
                )
                _log(db, employee_id, "edited", "; ".join(changes))
                db.commit()
                flash("Промените са записани.", "ok")
            return redirect(url_for("personnel.show", employee_id=employee_id))
        for message in errors:
            flash(message, "bad")
        employee = {**_view(row), **data}
    else:
        employee = _view(row)
    return render_template("pages/employee_form.html", employee=employee, positions=_positions(), events=_events(employee_id))


@bp.route("/sluzhiteli/<int:employee_id>/napuskane", methods=["GET", "POST"])
def remove(employee_id):
    row = _get(employee_id)
    if row["left_on"]:
        return redirect(url_for("personnel.edit", employee_id=employee_id))
    items = card_items_for(employee_id)
    if request.method == "POST":
        if items:
            flash("Служителят има зачислено имущество по картон. Първо го прехвърлете или бракувайте.", "bad")
            return redirect(url_for("personnel.remove", employee_id=employee_id))
        left_on = _parse_date(request.form.get("left_on")) or date.today().isoformat()
        reason = request.form.get("reason", "").strip()
        db = get_db()
        db.execute("UPDATE employees SET left_on = ?, leave_reason = ?, updated_at = ? WHERE id = ?",
                   (left_on, reason, _now(), employee_id))
        _log(db, employee_id, "left", reason)
        db.commit()
        flash(f"{full_name(row)} е преместен в „Бивши служители“.", "ok")
        return redirect(url_for("personnel.manage"))
    return render_template("pages/employee_remove.html", employee=_view(row), card_items=items)


@bp.route("/sluzhiteli/<int:employee_id>/vrashtane", methods=["POST"])
def restore(employee_id):
    row = _get(employee_id)
    if row["left_on"]:
        db = get_db()
        db.execute("UPDATE employees SET left_on = NULL, leave_reason = '', updated_at = ? WHERE id = ?", (_now(), employee_id))
        _log(db, employee_id, "restored", f"Напуснал на {date.fromisoformat(row['left_on']).strftime('%d.%m.%Y')}")
        db.commit()
        flash(f"{full_name(row)} е върнат в личния състав.", "ok")
    return redirect(url_for("personnel.manage"))


FIELD_LABELS = {"first_name": "Име", "middle_name": "Презиме", "last_name": "Фамилия", "position": "Длъжност",
                "hired_on": "Постъпил", "note": "Бележка"}


def _read_form():
    form = request.form
    data = {
        "first_name": normalize_name(form.get("first_name")),
        "middle_name": normalize_name(form.get("middle_name")),
        "last_name": normalize_name(form.get("last_name")),
        "position": normalize_position(form.get("position")),
        "hired_on": _parse_date(form.get("hired_on")),
        "note": form.get("note", "").strip(),
    }
    errors = [f"Попълнете „{FIELD_LABELS[key]}“." for key in ("first_name", "last_name", "position") if not data[key]]
    return data, errors


def _parse_date(value):
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except ValueError:
        return None


def _positions():
    rows = get_db().execute("SELECT DISTINCT position FROM employees").fetchall()
    return sorted((r[0] for r in rows), key=lambda p: (GROUP_ORDER.index(rank_group(p)), p.casefold()))


def _events(employee_id):
    rows = get_db().execute(
        "SELECT * FROM employee_events WHERE employee_id = ? ORDER BY happened_at DESC, id DESC", (employee_id,)
    ).fetchall()
    return [{**dict(r), "label": EVENT_LABELS.get(r["kind"], r["kind"]), "at": datetime.fromisoformat(r["happened_at"])}
            for r in rows]
