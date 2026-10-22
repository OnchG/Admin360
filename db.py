"""Обща SQLite база на приложението (файл instance/admin360.db).

Всеки модул описва своите таблици в собствен SCHEMA и ги създава при старт (виж personnel.init_db).
"""
import os
import sqlite3

from flask import current_app, g


def get_db():
    """Връзка към базата за текущата заявка (затваря се автоматично в края ѝ)."""
    if "db" not in g:
        os.makedirs(current_app.instance_path, exist_ok=True)
        g.db = sqlite3.connect(os.path.join(current_app.instance_path, "admin360.db"))
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)
