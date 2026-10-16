"""
db_tools.py - Python tool functions for Shelly the Virtual Waiter.

Every tool:
  * Validates its arguments (never trusts LLM output).
  * Reads/writes only via SQLite (single source of truth).
  * Returns a JSON-serializable dict: {"status", "message", ...extra}.
"""
from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from typing import Any

# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "restaurant.db")

SERVICE_FEE_RATE = 0.10  # 10% service fee (matches the mockup)
MAX_QTY_PER_LINE = 20    # sanity cap to reject absurd LLM inputs


# ------------------------------------------------------------
# Connection helper
# ------------------------------------------------------------
@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------
# Internal validation helpers
# ------------------------------------------------------------
def _ok(message: str, **extra) -> dict:
    return {"status": "success", "message": message, **extra}


def _err(message: str, **extra) -> dict:
    return {"status": "error", "message": message, **extra}


def _validate_qty(qty: Any) -> tuple[bool, int | str]:
    if isinstance(qty, bool):  # bool is subclass of int
        return False, "Quantity must be a number."
    try:
        qty_int = int(qty)
    except (TypeError, ValueError):
        return False, "Quantity must be a number."
    if qty_int <= 0:
        return False, "Quantity must be at least 1."
    if qty_int > MAX_QTY_PER_LINE:
        return False, f"Quantity cannot exceed {MAX_QTY_PER_LINE} per item."
    return True, qty_int


def _validate_session_open(conn, session_id: str) -> tuple[bool, str, dict | None]:
    if not isinstance(session_id, str) or not session_id.strip():
        return False, "Invalid session id.", None
    row = conn.execute(
        "SELECT * FROM table_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return False, f"Session '{session_id}' not found.", None
    session = dict(row)
    if session["status"] in ("CONFIRMED", "CANCELLED"):
        return False, f"Session is already {session['status']}. Start a new session to order again.", session
    return True, "", session


def _find_item_by_name(conn, name: str) -> sqlite3.Row | None:
    """Case-insensitive exact match first, then LIKE fallback."""
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    row = conn.execute(
        "SELECT * FROM menu_items WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    if row:
        return row
    row = conn.execute(
        "SELECT * FROM menu_items WHERE LOWER(name) LIKE LOWER(?)", (f"%{name}%",)
    ).fetchone()
    return row


def _suggest_alternatives(conn, category: str, exclude_id: int | None = None,
                          limit: int = 3) -> list[str]:
    q = "SELECT name FROM menu_items WHERE category = ? AND stock_qty > 0"
    params: list = [category]
    if exclude_id is not None:
        q += " AND id != ?"
        params.append(exclude_id)
    q += " LIMIT ?"
    params.append(limit)
    return [r["name"] for r in conn.execute(q, params).fetchall()]

def _row_to_item_dict(row) -> dict:
    """Convert a menu_items row to a dict with an explicit allergen display string."""
    d = dict(row)
    raw = (d.get("allergens") or "none").strip()
    if raw.lower() in ("", "none"):
        d["allergens_display"] = "No allergens recorded."
    else:
        d["allergens_display"] = f"CONTAINS: {raw}."
    return d

# ============================================================
# SESSION TOOLS
# ============================================================
def create_session(table_no: int, session_id: str | None = None) -> dict:
    """Create a new table session. Auto-generates session_id if not provided."""
    try:
        table_no = int(table_no)
    except (TypeError, ValueError):
        return _err("Table number must be an integer.")
    if table_no <= 0:
        return _err("Table number must be positive.")

    if session_id is None:
        # Simple deterministic-ish id: T{table}-{epoch-ish counter}
        with _connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM table_sessions").fetchone()["c"]
        session_id = f"T{table_no}-S{count + 1:03d}"
    elif not isinstance(session_id, str) or not session_id.strip():
        return _err("session_id must be a non-empty string.")

    with _connect() as conn:
        existing = conn.execute(
            "SELECT session_id FROM table_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing:
            return _err(f"Session '{session_id}' already exists.", session_id=session_id)
        conn.execute(
            "INSERT INTO table_sessions (session_id, table_no, status) VALUES (?, ?, 'OPEN')",
            (session_id, table_no),
        )
    return _ok(f"Session {session_id} opened for table {table_no}.",
               session_id=session_id, table_no=table_no, session_status="OPEN")


def get_session(session_id: str) -> dict:
    if not isinstance(session_id, str) or not session_id.strip():
        return _err("Invalid session id.")
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM table_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if not row:
        return _err(f"Session '{session_id}' not found.")
    return _ok("Session found.", session=dict(row))


# ============================================================
# MENU TOOLS
# ============================================================
def get_menu_by_category(category: str) -> dict:
    """Return all items in a category (case-insensitive)."""
    if not isinstance(category, str) or not category.strip():
        return _err("Category must be a non-empty string.")
    category = category.strip()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, category, description, ingredients, price, stock_qty, allergens "
            "FROM menu_items WHERE LOWER(category) = LOWER(?) ORDER BY name",
            (category,),
        ).fetchall()
        if not rows:
            # Provide list of valid categories to help the LLM recover
            cats = [r["category"] for r in conn.execute(
                "SELECT DISTINCT category FROM menu_items ORDER BY category"
            ).fetchall()]
            return _err(f"No items found in category '{category}'.",
                        valid_categories=cats)
    items = [_row_to_item_dict(r) for r in rows]
    return _ok(f"Found {len(items)} item(s) in {category}.", category=category, items=items)


def get_all_menu() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, category, description, ingredients, price, stock_qty, allergens "
            "FROM menu_items ORDER BY category, name"
        ).fetchall()
    return _ok("Full menu retrieved.", items=[_row_to_item_dict(r) for r in rows])


def get_item_by_name(item_name: str) -> dict:
    if not isinstance(item_name, str) or not item_name.strip():
        return _err("Item name must be a non-empty string.")
    with _connect() as conn:
        row = _find_item_by_name(conn, item_name)
    if not row:
        return _err(f"Item '{item_name}' not found on the menu.")
    return _ok(f"Found item '{row['name']}'.", item=_row_to_item_dict(row))


def check_item_allergen(item_id: int, allergen: str) -> dict:
    """Check whether the given menu item contains the specified allergen."""
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return _err("item_id must be an integer.")
    if not isinstance(allergen, str) or not allergen.strip():
        return _err("allergen must be a non-empty string.")

    allergen_l = allergen.strip().lower()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, name, allergens FROM menu_items WHERE id = ?", (item_id,)
        ).fetchone()
    if not row:
        return _err(f"Item with id {item_id} not found.")

    allergens_field = (row["allergens"] or "none").lower()
    tokens = [t.strip() for t in allergens_field.split(",") if t.strip()]
    contains = allergen_l in tokens
    return _ok(
        f"'{row['name']}' {'contains' if contains else 'does not contain'} {allergen}.",
        item_id=row["id"], item_name=row["name"],
        allergen=allergen, contains=contains,
        all_allergens=tokens,
    )

def search_menu_by_allergen(allergen: str, contain: bool = True) -> dict:
    """Return menu items that DO (contain=True) or DO NOT (contain=False)
    list the given allergen. Scans every menu item."""
    if not isinstance(allergen, str) or not allergen.strip():
        return _err("allergen must be a non-empty string.")
    allergen_l = allergen.strip().lower()

    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, category, description, ingredients, price, stock_qty, allergens "
            "FROM menu_items ORDER BY category, name"
        ).fetchall()

    matches: list[dict] = []
    for r in rows:
        tokens = [t.strip().lower() for t in (r["allergens"] or "").split(",") if t.strip()]
        has_it = allergen_l in tokens
        if (contain and has_it) or ((not contain) and not has_it):
            matches.append(_row_to_item_dict(r))

    if not matches:
        if contain:
            return _ok(f"No menu items contain {allergen}.", allergen=allergen,
                       contain=contain, items=[])
        return _ok(f"All menu items contain {allergen}.", allergen=allergen,
                   contain=contain, items=[])

    verb = "contain" if contain else "do not contain"
    return _ok(f"{len(matches)} item(s) {verb} {allergen}.",
               allergen=allergen, contain=contain, items=matches)



def check_item_availability(item_name: str, qty: int = 1) -> dict:
    ok, qty_or_msg = _validate_qty(qty)
    if not ok:
        return _err(qty_or_msg)
    with _connect() as conn:
        row = _find_item_by_name(conn, item_name)
        if not row:
            return _err(f"Item '{item_name}' not found on the menu.")
        if row["stock_qty"] < qty_or_msg:
            alts = _suggest_alternatives(conn, row["category"], exclude_id=row["id"])
            return _err(
                f"Sorry, only {row['stock_qty']} of '{row['name']}' are available.",
                item_id=row["id"], item_name=row["name"],
                requested=qty_or_msg, stock_qty=row["stock_qty"],
                alternatives=alts, category=row["category"],
            )
    return _ok(f"'{row['name']}' is available (stock: {row['stock_qty']}).",
               item_id=row["id"], item_name=row["name"], stock_qty=row["stock_qty"])


# ============================================================
# CART TOOLS
# ============================================================
def add_item_to_order(session_id: str, item_name: str, qty: int,
                      instructions: str = "") -> dict:
    ok, qty_or_msg = _validate_qty(qty)
    if not ok:
        return _err(qty_or_msg)
    qty = qty_or_msg
    if not isinstance(instructions, str):
        instructions = str(instructions)
    instructions = instructions.strip()

    with _connect() as conn:
        ok_s, msg, session = _validate_session_open(conn, session_id)
        if not ok_s:
            return _err(msg)
        item = _find_item_by_name(conn, item_name)
        if not item:
            return _err(f"Item '{item_name}' not found on the menu.")

        # Available stock = stock_qty - already in cart for other lines of same item
        already = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) AS q FROM order_line_items "
            "WHERE session_id = ? AND item_id = ?",
            (session_id, item["id"]),
        ).fetchone()["q"]
        if item["stock_qty"] <= 0 or already + qty > item["stock_qty"]:
            alts = _suggest_alternatives(conn, item["category"], exclude_id=item["id"])
            return _err(
                f"Sorry, '{item['name']}' is out of stock or not enough left.",
                item_id=item["id"], item_name=item["name"],
                available=max(item["stock_qty"] - already, 0),
                alternatives=alts, category=item["category"],
            )

        # Merge with an existing line only if same item AND same instructions
        existing = conn.execute(
            "SELECT line_id, quantity FROM order_line_items "
            "WHERE session_id = ? AND item_id = ? AND special_instructions = ?",
            (session_id, item["id"], instructions),
        ).fetchone()
        if existing:
            new_qty = existing["quantity"] + qty
            if new_qty > MAX_QTY_PER_LINE:
                return _err(f"Cannot exceed {MAX_QTY_PER_LINE} of the same item.")
            conn.execute(
                "UPDATE order_line_items SET quantity = ? WHERE line_id = ?",
                (new_qty, existing["line_id"]),
            )
            line_id = existing["line_id"]
        else:
            cur = conn.execute(
                "INSERT INTO order_line_items "
                "(session_id, item_id, quantity, special_instructions) "
                "VALUES (?, ?, ?, ?)",
                (session_id, item["id"], qty, instructions),
            )
            line_id = cur.lastrowid

        # Move session to REVIEWING once there's something in the cart
        conn.execute(
            "UPDATE table_sessions SET status = 'REVIEWING' "
            "WHERE session_id = ? AND status = 'OPEN'",
            (session_id,),
        )

    order = view_current_order(session_id)
    return _ok(
        f"Added {qty} x {item['name']}"
        + (f" ({instructions})" if instructions else "")
        + " to your order.",
        line_id=line_id,
        item_name=item["name"],
        qty=qty,
        instructions=instructions,
        order=order.get("order"),
    )


def change_item_quantity(session_id: str, item_name: str, new_qty: int,
                         instructions: str | None = None) -> dict:
    """Set an existing line's quantity to new_qty (absolute value)."""
    ok, qty_or_msg = _validate_qty(new_qty)
    if not ok:
        return _err(qty_or_msg)
    new_qty = qty_or_msg

    with _connect() as conn:
        ok_s, msg, _ = _validate_session_open(conn, session_id)
        if not ok_s:
            return _err(msg)
        item = _find_item_by_name(conn, item_name)
        if not item:
            return _err(f"Item '{item_name}' not found on the menu.")

        q = ("SELECT line_id, quantity, special_instructions FROM order_line_items "
             "WHERE session_id = ? AND item_id = ?")
        params: list = [session_id, item["id"]]
        if instructions is not None:
            q += " AND special_instructions = ?"
            params.append(instructions.strip())
        lines = conn.execute(q, params).fetchall()
        if not lines:
            return _err(f"'{item['name']}' is not in your current order.")
        if len(lines) > 1 and instructions is None:
            return _err(
                f"Multiple lines of '{item['name']}' exist with different instructions. "
                "Specify which one, or remove them and re-add.",
                lines=[dict(l) for l in lines],
            )

        line = lines[0]
        if new_qty > item["stock_qty"]:
            return _err(f"Only {item['stock_qty']} of '{item['name']}' are available.")
        conn.execute(
            "UPDATE order_line_items SET quantity = ? WHERE line_id = ?",
            (new_qty, line["line_id"]),
        )

    order = view_current_order(session_id)
    return _ok(f"Updated '{item['name']}' to quantity {new_qty}. "
               f"Your new total is ₱{order['order']['grand_total']:.2f}.",
               item_name=item["name"], new_qty=new_qty,
               order=order.get("order"))


def remove_item_from_order(session_id: str, item_name: str,
                           instructions: str | None = None) -> dict:
    with _connect() as conn:
        ok_s, msg, _ = _validate_session_open(conn, session_id)
        if not ok_s:
            return _err(msg)
        item = _find_item_by_name(conn, item_name)
        if not item:
            return _err(f"Item '{item_name}' not found on the menu.")

        q = ("SELECT line_id, quantity FROM order_line_items "
             "WHERE session_id = ? AND item_id = ?")
        params: list = [session_id, item["id"]]
        if instructions is not None:
            q += " AND special_instructions = ?"
            params.append(instructions.strip())
        lines = conn.execute(q, params).fetchall()
        if not lines:
            return _err(f"'{item['name']}' is not in your current order.")

        for l in lines:
            conn.execute("DELETE FROM order_line_items WHERE line_id = ?", (l["line_id"],))

    order = view_current_order(session_id)
    return _ok(f"Removed '{item['name']}' from your order.",
               item_name=item["name"], order=order.get("order"))

def clear_session_cart(session_id: str) -> dict:
    with _connect() as conn:
        ok_s, msg, _ = _validate_session_open(conn, session_id)
        if not ok_s:
            return _err(msg)
        conn.execute("DELETE FROM order_line_items WHERE session_id = ?", (session_id,))
    order = view_current_order(session_id)
    return _ok("Cart cleared.", order=order.get("order"))


def view_current_order(session_id: str) -> dict:
    with _connect() as conn:
        sess = conn.execute(
            "SELECT * FROM table_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not sess:
            return _err(f"Session '{session_id}' not found.")
        rows = conn.execute(
            "SELECT li.line_id, mi.id AS item_id, mi.name, mi.category, mi.price, "
            "       li.quantity, li.special_instructions "
            "FROM order_line_items li "
            "JOIN menu_items mi ON mi.id = li.item_id "
            "WHERE li.session_id = ? ORDER BY li.line_id",
            (session_id,),
        ).fetchall()

    lines = []
    subtotal = 0.0
    for r in rows:
        line_total = round(r["price"] * r["quantity"], 2)
        subtotal += line_total
        lines.append({
            "line_id": r["line_id"],
            "item_id": r["item_id"],
            "name": r["name"],
            "category": r["category"],
            "price": r["price"],
            "quantity": r["quantity"],
            "instructions": r["special_instructions"] or "",
            "line_total": line_total,
        })
    subtotal = round(subtotal, 2)
    service_fee = round(subtotal * SERVICE_FEE_RATE, 2)
    grand_total = round(subtotal + service_fee, 2)

    order = {
        "session_id": session_id,
        "table_no": sess["table_no"],
        "status": sess["status"],
        "lines": lines,
        "subtotal": subtotal,
        "service_fee": service_fee,
        "grand_total": grand_total,
        "item_count": sum(l["quantity"] for l in lines),
    }
    msg = "Your order is empty." if not lines else f"You have {order['item_count']} item(s)."
    return _ok(msg, order=order)


def calculate_totals(session_id: str) -> dict:
    res = view_current_order(session_id)
    if res["status"] != "success":
        return res
    o = res["order"]
    return _ok(f"Subtotal ₱{o['subtotal']:.2f}, service fee ₱{o['service_fee']:.2f}, "
               f"grand total ₱{o['grand_total']:.2f}.",
               subtotal=o["subtotal"], service_fee=o["service_fee"],
               grand_total=o["grand_total"])


# ============================================================
# FINALIZATION TOOLS
# ============================================================
def confirm_order(session_id: str) -> dict:
    """Lock the order, deduct stock, mark session CONFIRMED."""
    with _connect() as conn:
        sess = conn.execute(
            "SELECT * FROM table_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not sess:
            return _err(f"Session '{session_id}' not found.")
        if sess["status"] == "CONFIRMED":
            return _err("This order has already been confirmed.")
        if sess["status"] == "CANCELLED":
            return _err("This order was cancelled and cannot be confirmed.")

        lines = conn.execute(
            "SELECT li.item_id, li.quantity, mi.name, mi.stock_qty "
            "FROM order_line_items li JOIN menu_items mi ON mi.id = li.item_id "
            "WHERE li.session_id = ?",
            (session_id,),
        ).fetchall()
        if not lines:
            return _err("Cannot confirm an empty order.")

        # Re-verify stock at confirmation time
        for l in lines:
            if l["stock_qty"] < l["quantity"]:
                return _err(
                    f"'{l['name']}' only has {l['stock_qty']} left; "
                    f"please adjust the quantity before confirming."
                )

        # Deduct stock and lock the session
        for l in lines:
            conn.execute(
                "UPDATE menu_items SET stock_qty = stock_qty - ? WHERE id = ?",
                (l["quantity"], l["item_id"]),
            )
        conn.execute(
            "UPDATE table_sessions SET status = 'CONFIRMED', "
            "confirmed_at = datetime('now') WHERE session_id = ?",
            (session_id,),
        )

    receipt = view_current_order(session_id)["order"]
    return _ok("Order confirmed. Thank you!", receipt=receipt)


def cancel_order(session_id: str) -> dict:
    with _connect() as conn:
        sess = conn.execute(
            "SELECT * FROM table_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not sess:
            return _err(f"Session '{session_id}' not found.")
        if sess["status"] == "CONFIRMED":
            return _err("Cannot cancel a confirmed order.")
        if sess["status"] == "CANCELLED":
            return _err("This order is already cancelled.")
        conn.execute("DELETE FROM order_line_items WHERE session_id = ?", (session_id,))
        conn.execute(
            "UPDATE table_sessions SET status = 'CANCELLED' WHERE session_id = ?",
            (session_id,),
        )
    return _ok("Order cancelled.", session_id=session_id)


# ============================================================
# OLLAMA TOOL SCHEMAS  (for /api/chat "tools" parameter)
# ============================================================
TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "get_item_by_name",
        "description": "Look up a single menu item by name. Returns description, ingredients, price, stock, and recorded allergens. ALWAYS call this before answering ingredient or allergen questions about a specific dish.",
        "parameters": {"type": "object", "properties": {
            "item_name": {"type": "string", "description": "Menu item name, e.g. 'Chicken Adobo'."}
        }, "required": ["item_name"]}}},

    {"type": "function", "function": {
        "name": "get_menu_by_category",
        "description": "List all menu items in a category (Appetizers, Main Dishes, Rice, Drinks, Desserts).",
        "parameters": {"type": "object", "properties": {
            "category": {"type": "string", "description": "Menu category name."}
        }, "required": ["category"]}}},

    {"type": "function", "function": {
        "name": "check_item_allergen",
        "description": "Check whether a menu item contains a specific allergen.",
        "parameters": {"type": "object", "properties": {
            "item_id": {"type": "integer", "description": "Menu item id."},
            "allergen": {"type": "string", "description": "Allergen name, e.g. peanuts, dairy, gluten, seafood, soy, fish, eggs."}
        }, "required": ["item_id", "allergen"]}}},

    {"type": "function", "function": {
        "name": "search_menu_by_allergen",
        "description": "Scan the FULL menu and return all items that contain (or do not contain) a given allergen. Use this when the customer asks 'Do you have anything with peanuts?' or 'What can I eat that is gluten-free?'.",
        "parameters": {"type": "object", "properties": {
            "allergen": {"type": "string", "description": "Allergen name, e.g. peanuts, dairy, gluten, seafood, soy, fish, eggs."},
            "contain": {"type": "boolean", "description": "true = return items that CONTAIN the allergen; false = return items that DO NOT contain it. Default true."}
        }, "required": ["allergen"]}}},

    {"type": "function", "function": {
        "name": "add_item_to_order",
        "description": "Add a menu item to the current table's order, or increase its quantity. Rejects out-of-stock items.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string"},
            "item_name": {"type": "string"},
            "qty": {"type": "integer", "minimum": 1, "maximum": 20},
            "instructions": {"type": "string", "description": "Special instructions, e.g. 'No onions'. Use empty string if none."}
        }, "required": ["session_id", "item_name", "qty"]}}},

    {"type": "function", "function": {
        "name": "view_current_order",
        "description": "Show the current cart contents and totals for a session.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string"}
        }, "required": ["session_id"]}}},

    {"type": "function", "function": {
        "name": "change_item_quantity",
        "description": "Set an existing line item's quantity to a new absolute value.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string"},
            "item_name": {"type": "string"},
            "new_qty": {"type": "integer", "minimum": 1, "maximum": 20}
        }, "required": ["session_id", "item_name", "new_qty"]}}},

    {"type": "function", "function": {
        "name": "remove_item_from_order",
        "description": "Remove an item entirely from the current order.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string"},
            "item_name": {"type": "string"}
        }, "required": ["session_id", "item_name"]}}},

    {"type": "function", "function": {
        "name": "check_item_availability",
        "description": "Check if an item has enough stock before adding.",
        "parameters": {"type": "object", "properties": {
            "item_name": {"type": "string"},
            "qty": {"type": "integer", "minimum": 1}
        }, "required": ["item_name"]}}},

    {"type": "function", "function": {
        "name": "calculate_totals",
        "description": "Return subtotal, service fee, and grand total for the current session.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string"}
        }, "required": ["session_id"]}}},

    {"type": "function", "function": {
        "name": "confirm_order",
        "description": "Confirm and finalize the order. Deducts stock and locks the session.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string"}
        }, "required": ["session_id"]}}},

    {"type": "function", "function": {
        "name": "cancel_order",
        "description": "Cancel the entire order and clear the cart.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string"}
        }, "required": ["session_id"]}}},
]


# Map tool name -> callable (used by the FSM to execute Qwen's requests)
TOOL_REGISTRY: dict[str, callable] = {
    "create_session":           create_session,
    "get_menu_by_category":     get_menu_by_category,
    "get_all_menu":             get_all_menu,
    "get_item_by_name":         get_item_by_name,
    "check_item_allergen":      check_item_allergen,
    "search_menu_by_allergen":  search_menu_by_allergen,
    "check_item_availability":  check_item_availability,
    "add_item_to_order":        add_item_to_order,
    "change_item_quantity":     change_item_quantity,
    "remove_item_from_order":   remove_item_from_order,
    "clear_session_cart":       clear_session_cart,
    "view_current_order":       view_current_order,
    "calculate_totals":         calculate_totals,
    "confirm_order":            confirm_order,
    "cancel_order":             cancel_order,
}