"""Smoke tests for db_tools.py - verifies each tool in isolation."""
import json
import db_tools as db

def show(label, res):
    print(f"\n=== {label} ===")
    print(json.dumps(res, indent=2, ensure_ascii=False))

# Start clean: reset DB
import subprocess, sys
subprocess.run([sys.executable, "init_db.py"], check=True)

# 1. Create a session
r = db.create_session(table_no=4)
show("create_session", r)
session_id = r["session_id"]

# 2. Browse a category
show("get_menu_by_category(Main Dishes)",
     db.get_menu_by_category("Main Dishes"))

# 3. Unknown category (recovery hints)
show("get_menu_by_category(Pizza)",
     db.get_menu_by_category("Pizza"))

# 4. Add items
show("add 2 Chicken Adobo (no onions)",
     db.add_item_to_order(session_id, "Chicken Adobo", 2, "no onions"))
show("add 1 Iced Tea",
     db.add_item_to_order(session_id, "Iced Tea", 1))
show("add 1 Garlic Fried Rice",
     db.add_item_to_order(session_id, "Garlic Fried Rice", 1))

# 5. Edge: out-of-stock item
show("add Vegetable Spring Rolls (out of stock)",
     db.add_item_to_order(session_id, "Vegetable Spring Rolls", 1))

# 6. Edge: unknown item
show("add Pizza Margherita (not on menu)",
     db.add_item_to_order(session_id, "Pizza Margherita", 1))

# 7. Edge: invalid quantity
show("add Chicken Adobo qty=0",  db.add_item_to_order(session_id, "Chicken Adobo", 0))
show("add Chicken Adobo qty=-5", db.add_item_to_order(session_id, "Chicken Adobo", -5))
show("add Chicken Adobo qty=999",db.add_item_to_order(session_id, "Chicken Adobo", 999))

# 8. Allergen check
show("check Chicken Adobo for soy (should be True)",
     db.check_item_allergen(4, "soy"))
show("check Chicken Adobo for peanuts (should be False)",
     db.check_item_allergen(4, "peanuts"))
show("check Beef Kare-Kare for peanuts (should be True)",
     db.check_item_allergen(5, "peanuts"))

# 9. Change quantity
show("change Iced Tea to qty=3",
     db.change_item_quantity(session_id, "Iced Tea", 3))

# 10. Remove item
show("remove Garlic Fried Rice",
     db.remove_item_from_order(session_id, "Garlic Fried Rice"))

# 11. View current order
show("view_current_order",
     db.view_current_order(session_id))

# 12. Confirm
show("confirm_order", db.confirm_order(session_id))

# 13. Double-confirm (should fail)
show("confirm_order again (should error)", db.confirm_order(session_id))

# 14. Try to modify after confirm (should fail)
show("add item after confirm (should error)",
     db.add_item_to_order(session_id, "Iced Tea", 1))

# 15. Verify stock was deducted
show("Mango Graham stock check (was 2, untouched)",
     db.check_item_availability("Mango Graham", 2))
show("Chicken Adobo stock check (was 25, minus 2 = 23)",
     db.check_item_availability("Chicken Adobo", 23))

print("\n*** All tool tests completed. ***")