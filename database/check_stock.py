import sqlite3
c = sqlite3.connect("restaurant.db")
for name in ("Chicken Adobo", "Iced Tea", "Mango Graham"):
    row = c.execute(
        "SELECT name, stock_qty FROM menu_items WHERE name = ?", (name,)
    ).fetchone()
    print(row)
c.close()