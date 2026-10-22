# Admin360

Платформа за деловодство, счетоводство, лични картони на служителите, склад и инвентар.
Засега има общата рамка от Dams360 (ляво меню, header, footer, тъмна тема) и примерни страници;
модулите от менюто са празни страници, които ще се изграждат една по една.

## Стартиране (Windows)

```
py -m pip install -r requirements.txt
py app.py
```

Отворете http://127.0.0.1:5000

## Какво се сменя къде

| Какво | Файл |
|---|---|
| Име на проекта, надпис под логото, автор във footer-а | `config.py` (`APP_NAME`, `APP_TAGLINE`, `APP_AUTHOR`) |
| Пунктове в лявото меню | `config.py` (`MENU`) |
| Иконки за менюто | `templates/components/icons.html` |
| Знак на логото | `templates/components/brand_logo.html` |
| Цветове на темата | `static/css/app-theme.css` (токените най-горе: `--c-bg`, `--c-accent` …) |
| Карти, таблици, форми | `static/css/app-content.css` |

## Нова страница

1. Шаблон в `templates/pages/`, който започва така:

```
{% extends "layouts/app_shell.html" %}
{% set sidebar_active = 'moiat_kluch' %}
{% set header_title = 'Заглавие' %}
{% set header_breadcrumbs = [{'label': 'Начало', 'url': url_for('dashboard')}, {'label': 'Заглавие'}] %}
{% block content %}
<div class="page">
    <section class="dash-card">...</section>
</div>
{% endblock %}
```

2. Маршрут в `app.py`:

```python
@app.route("/moiata-stranica")
def moiata_stranica():
    return render_template("pages/moiata_stranica.html")
```

3. Пункт в `MENU` в `config.py`: `{"key": "moiat_kluch", "label": "Моята страница", "icon": "document", "endpoint": "moiata_stranica"}`.

Всяка страница ползва общата рамка (`layouts/app_shell.html`). Не правете страници със собствено меню.

## Готови елементи

- Поле за дата с български календар: `{% from "components/date_input.html" import date_input %}{{ date_input('date', today) }}`
- Филтри `bgdate` (дд.мм.гггг) и `bgnum` (десетична запетая)
- Незаписани промени: предупреждение при напускане, Ctrl+S записва, Enter минава към следващото поле в таблица (`static/js/app-ux.js`)
- Поле за файл на български („Избери файл“)
