"""
init_db.py - Initializes restaurant.db from init_restaurant_db.sql
Run once:  python init_db.py
Re-run anytime to reset the database to a clean state.
"""
import sqlite3
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SQL_FILE = os.path.join(HERE, "init_restaurant_db.sql")
DB_FILE = os.path.join(HERE, "restaurant.db")

def init_database():
    if not os.path.exists(SQL_FILE):
        raise FileNotFoundError(f"Missing SQL file: {SQL_FILE}")

    with open(SQL_FILE, "r", encoding="utf-8") as f:
        sql_script = f.read()

    conn = sqlite3.connect(DB_FILE)
    try:
        conn.executescript(sql_script)
        conn.commit()
    finally:
        conn.close()

    # Verification
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM menu_items")
    menu_count = cur.fetchone()[0]
    cur.execute("SELECT category, COUNT(*) FROM menu_items GROUP BY category")
    cats = cur.fetchall()
    cur.execute("SELECT name, stock_qty FROM menu_items WHERE stock_qty = 0")
    out_of_stock = cur.fetchall()
    conn.close()

    print(f"[OK] Database created: {DB_FILE}")
    print(f"[OK] Menu items inserted: {menu_count}")
    for cat, n in cats:
        print(f"     - {cat}: {n} items")
    print(f"[OK] Out-of-stock items (for testing): {[n for n,_ in out_of_stock]}")

if __name__ == "__main__":
    init_database()