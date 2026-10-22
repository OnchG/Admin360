"""Настройки на приложението: име, автор и ляво меню.

Това е единственото място, където се сменят името на проекта и пунктовете в менюто.
"""

APP_NAME = "Admin360"            # показва се в логото, заглавието на раздела и footer-а
APP_TAGLINE = "Деловодство · Счетоводство · Склад"  # малкият надпис под името в логото
APP_AUTHOR = "Онник Гюлян"       # втори ред във footer-а; празно = скрито

# Текущ потребител (докато няма вход с парола).

# Ляво меню. Всеки пункт: key (уникален), label, icon (виж templates/components/icons.html)
# и endpoint (име на Flask функцията) или url. Група = {"label", "icon", "items": [...]}.
# Пунктовете с endpoint "placeholder" водят към празна страница, докато модулът се изгради.
def _page(slug, label, icon):
    return {"key": slug, "label": label, "icon": icon, "endpoint": "placeholder", "args": {"slug": slug}}


MENU = [
    {"key": "dashboard", "label": "Табло", "icon": "home", "endpoint": "dashboard"},
    {"label": "Деловодство", "icon": "document", "items": [
        _page("vhodyashti", "Входящи", "inbox"),
        _page("izhodyashti", "Изходящи", "outbox"),
        _page("dogovori", "Договори", "edit"),
    ]},
    {"label": "Счетоводство", "icon": "coins", "items": [
        _page("fakturi", "Фактури", "list"),
        _page("plashtania", "Плащания", "coins"),
    ]},
    {"label": "Персонал", "icon": "users", "items": [
        {"key": "sluzhiteli", "label": "Служители", "icon": "users", "endpoint": "personnel.employees"},
        {"key": "kartoni", "label": "Картони", "icon": "card", "endpoint": "cards.index"},
    ]},
    {"label": "Склад", "icon": "box", "items": [
        {"key": "nalichnosti", "label": "Наличности", "icon": "box", "endpoint": "stock.items"},
        {"key": "avariyen_rezerv", "label": "Авариен резерв", "icon": "shield", "endpoint": "reserve.index"},
        _page("dvizhenia", "Движения", "swap"),
        _page("inventarizacia", "Инвентаризация", "check"),
    ]},
    {"key": "spravki", "label": "Справки", "icon": "chart", "endpoint": "placeholder", "args": {"slug": "spravki"}},
    # само за администратор (виж auth.py)
    {"key": "admin", "label": "Админ панел", "icon": "settings", "endpoint": "auth.admin_panel", "admin": True},
]

# Заглавия на страниците без съдържание (slug -> заглавие), взети от менюто.
PLACEHOLDERS = {
    item["args"]["slug"]: item["label"]
    for entry in MENU
    for item in entry.get("items", [entry])
    if item.get("endpoint") == "placeholder"
}
