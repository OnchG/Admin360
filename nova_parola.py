"""Нова парола за потребител, когато старата е забравена (пуска се на компютъра със системата).

    py nova_parola.py            -> за потребителя onnik
    py nova_parola.py ivan       -> за друг потребител

Паролата се пита два пъти и не се вижда, докато се пише. Потребителят се активира, ако е бил спрян.
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

from app import app
from auth import MIN_PASSWORD, OWNER_USERNAME
from db import get_db


def main():
    username = sys.argv[1] if len(sys.argv) > 1 else OWNER_USERNAME
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None:
            names = ", ".join(r["username"] for r in db.execute("SELECT username FROM users ORDER BY username"))
            print(f"Няма потребител „{username}“. Потребители: {names or 'няма'}")
            return 1
        password = getpass.getpass(f"Нова парола за {user['username']}: ")
        if len(password) < MIN_PASSWORD:
            print(f"Паролата трябва да е поне {MIN_PASSWORD} знака.")
            return 1
        if getpass.getpass("Повторете паролата: ") != password:
            print("Двете пароли не съвпадат.")
            return 1
        db.execute("UPDATE users SET password_hash = ?, active = 1 WHERE id = ?",
                   (generate_password_hash(password), user["id"]))
        db.commit()
        print(f"Паролата на {user['username']} е сменена. Влезте с новата парола.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
