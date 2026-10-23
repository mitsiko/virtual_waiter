"""
fsm_agent.py - Finite State Machine controller for Shelly the Virtual Waiter.
"""
from __future__ import annotations
import os
import sys
import json
import re
import requests
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(os.path.dirname(HERE), "database")
if DB_DIR not in sys.path:
    sys.path.insert(0, DB_DIR)

import db_tools as db  # noqa: E402

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:3b"
MAX_TOOL_ITERATIONS = 6
REQUEST_TIMEOUT = 120

STATES = [
    "STATE_GREETING",
    "STATE_TAKING_ORDER",
    "STATE_REVIEW",
    "STATE_CONFIRMED",
    "STATE_COMPLETED",
]

REVIEW_KEYWORDS = [
    "review", "bill", "check", "see my order", "show my order",
    "what did i order", "summary", "receipt",
]
CONFIRM_KEYWORDS = [
    "confirm", "place the order", "place my order", "that's all",
    "thats all", "finalize", "yes that's it", "yes thats it",
]
ADD_INTENT_TRIGGERS = [
    "add", "i'd like", "i would like", "i'll have", "ill have",
    "get me", "order me", "bring me", "i want",
]
REMOVE_INTENT_TRIGGERS = [
    "remove", "delete", "take out", "i don't want", "i dont want",
    "no more", "cancel the", "cancel my",
]
CANCEL_ORDER_TRIGGERS = [
    "cancel my order", "cancel the order", "cancel the whole order",
    "cancel my whole order", "cancel everything", "cancel all of it",
    "cancel all", "forget it, cancel", "forget the order",
]
RESTAURANT_WHITELIST = [
    "menu", "food", "dish", "meal", "eat", "drink", "order", "table",
    "waiter", "waitress", "server", "manager", "staff", "restaurant",
    "bistro", "shelly", "kitchen", "chef", "serve", "prepared", "cook",
    "appetizer", "appetizers", "starter", "starters",
    "main", "mains", "entree", "side", "sides",
    "rice", "dessert", "desserts", "drinks",
    "bill", "receipt", "checkout", "check out", "total", "subtotal",
    "service fee", "tip", "reserve", "reservation", "hours", "open",
    "closed", "cancel", "confirm", "add", "remove", "change",
    "modify", "note", "instruction", "instructions",
    "allergen", "allergens", "ingredient", "ingredients",
    "peanut", "peanuts", "dairy", "gluten", "soy", "seafood",
    "fish", "egg", "eggs", "vegetarian", "vegan", "spicy",
    "sauce", "raw",
    "chicken", "beef", "pork", "adobo", "kare-kare", "karekare",
    "sinigang", "bangus", "pad thai", "padthai", "lumpia",
    "calamari", "halo-halo", "halohalo", "leche flan", "lecheflan",
    "mango graham", "mangograham", "buko", "calamansi",
    "iced tea", "icedtea", "spring rolls", "springrolls",
    "garlic fried rice", "white rice", "steamed rice",
    "price", "cost", "how much", "peso", "pesos", "₱", "php",
    "yes", "no", "okay", "ok", "sure", "please", "thanks", "thank you",
    "hi", "hello", "hey",
]

OFF_TOPIC_TRIGGERS = [
    "capital of", "population of", "president of", "prime minister",
    "weather", "temperature outside", "temperature in",
    "translate", "write a poem", "write a story", "write me a",
    "tell me a joke", "tell me a story", "tell me about the",
    "what year was", "what year did", "how old is the",
    "how tall is", "how far is",
    "meaning of life", "meaning of the universe",
    "what's 2+2", "what is 2+2",
    "in python", "in java", "in javascript", "in c++",
    "algorithm for", "sorting algorithm",
    "who won the", "won the election",
]
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


# ------------------------------------------------------------
# System prompt
# ------------------------------------------------------------
BASE_PERSONA = """You are Shelly, the friendly virtual waiter at Perpetual Bistro.
You help guests browse the menu, customize dishes, and finalize their order.

STRICT RULES:

1. SCOPE
   You ONLY discuss Perpetual Bistro. If the user asks about ANYTHING else,
   reply EXACTLY: "I'm here to help with your Perpetual Bistro order. What would you like to eat or drink?"

2. NO FABRICATION
   NEVER invent menu items, prices, stock, allergens, ingredients, or order contents.
   Every menu or order fact MUST come from a tool call.

3. INGREDIENTS vs ALLERGENS
   For questions about what a dish contains or whether it has a specific allergen:
     * A specific dish ("Does Chicken Adobo have dairy?") -> call get_item_by_name.
     * "Do you have anything with X?" / "What's gluten-free?" -> call
       search_menu_by_allergen(allergen=X, contain=true or false).
   The "allergens_display" field is authoritative.

4. UNSUPPORTED CUSTOMIZATIONS
   The restaurant does NOT support removing ingredients or making a dish
   vegetarian. Reply: "I'm sorry, our kitchen can't modify that dish's recipe.
   I can add a special note to your order but the dish will be prepared as described."
   If the customer accepts the note-only alternative and the item is already in
   the cart, call update_item_instructions with the request as the note.
   Never claim the recipe was changed.

5. SPECIAL INSTRUCTIONS
   Notes like "no onions", "less ice", "extra spicy" go into special_instructions.

5b. CHANGING NOTES
   If the customer asks to change, update, or replace the note on an item already
   in the order (for example: "change the note on the Chicken Adobo to extra sauce"),
   call update_item_instructions(session_id, item_name, new_instructions).
   Do NOT add a second copy of the item. Do NOT delete and re-add it.
   The previous note is replaced entirely.

6. AMBIGUOUS REQUESTS
   If you cannot tell which item or quantity the customer means, ASK a short
   clarifying question.

7. ORDER FLOW
   * Modify the order only via tools.
   * When the customer asks to see the bill, call view_current_order and present
     an itemized breakdown with subtotal, service fee, and grand total.
   * Only call confirm_order when the customer EXPLICITLY confirms AND the FSM
     state is REVIEW.

8. STYLE
   Keep replies SHORT and natural (1-3 sentences). Speak in first person to
   the customer ("I've added...", not "The customer added...").

9. CURRENCY
   Prices are in Philippine Pesos. ALWAYS write amounts using the symbol ₱
   (example: ₱320.00). NEVER use $, USD, or any other currency symbol.
   If you are about to type $, use ₱ instead.

Menu categories: Appetizers, Main Dishes, Rice, Drinks, Desserts.
"""


def _state_instructions(state: str, session_id: str | None, table_no: int | None) -> str:
    if state == "STATE_GREETING":
        return (
            f"\nSTATE: GREETING. No table yet. Do not call any tool. "
            f"Just welcome the guest and ask for their table number."
        )
    if state == "STATE_TAKING_ORDER":
        return (
            f"\nSTATE: TAKING_ORDER. Session: {session_id} at Table {table_no}. "
            f"Help browse the menu and modify the order. "
            f"Always use session_id='{session_id}'."
        )
    if state == "STATE_REVIEW":
        return (
            f"\nSTATE: REVIEW. Session: {session_id} at Table {table_no}. "
            f"The order is NOT yet confirmed. Present the itemized breakdown "
            f"with subtotal, service fee, and grand total, then ask: "
            f"'Shall I confirm this order, or would you like to change anything?' "
            f"Do NOT say the order is confirmed while in this state."
        )
    if state == "STATE_CONFIRMED":
        return (
            f"\nSTATE: CONFIRMED. Session: {session_id}. "
            f"The order is locked. Thank the customer and say goodbye."
        )
    if state == "STATE_COMPLETED":
        return (
            f"\nSTATE: COMPLETED. Session: {session_id} is closed. "
            f"Politely invite the customer to start a new order if they wish."
        )
    return ""


# ------------------------------------------------------------
# ShellyAgent
# ------------------------------------------------------------
class ShellyAgent:
    def __init__(self, session_id: str | None = None, table_no: int | None = None):
        self.state: str = "STATE_GREETING"
        self.session_id: str | None = session_id
        self.table_no: int | None = table_no
        self.messages: list[dict] = []
        self._pending_ambiguous_add: bool = False
        self._pending_cancel: bool = False
        self._rebuild_system_prompt()

    def _rebuild_system_prompt(self):
        sys_content = BASE_PERSONA + _state_instructions(
            self.state, self.session_id, self.table_no
        )
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = sys_content
        else:
            self.messages.insert(0, {"role": "system", "content": sys_content})

    def _set_state(self, new_state: str):
        if new_state not in STATES:
            raise ValueError(f"Unknown state: {new_state}")
        if new_state != self.state:
            self.state = new_state
            self._rebuild_system_prompt()

    # -------- Ollama call --------
    def _call_ollama(self) -> dict:
        payload = {
            "model": OLLAMA_MODEL,
            "messages": self.messages,
            "tools": db.TOOL_SCHEMAS,
            "stream": False,
            "options": {"temperature": 0.4},
        }
        try:
            r = requests.post(OLLAMA_URL, json=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Cannot reach Ollama at http://localhost:11434. Start it with: ollama serve")
        except requests.exceptions.Timeout:
            raise RuntimeError("Ollama request timed out.")
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Ollama HTTP error: {e}")

    # -------- Tool execution --------
    def _execute_tool_call(self, name: str, arguments: Any) -> dict:
        fn = db.TOOL_REGISTRY.get(name)
        if fn is None:
            return {"status": "error", "message": f"Tool '{name}' is not registered."}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return {"status": "error",
                        "message": f"Could not parse arguments for {name}: {arguments}"}
        if not isinstance(arguments, dict):
            return {"status": "error",
                    "message": f"Arguments for {name} must be a JSON object."}

        session_tools = {"add_item_to_order", "view_current_order",
                         "change_item_quantity", "remove_item_from_order",
                         "calculate_totals", "confirm_order", "cancel_order",
                         "update_item_instructions"}
        if name in session_tools and self.session_id:
            arguments["session_id"] = self.session_id

        if name == "confirm_order" and self.state != "STATE_REVIEW":
            return {"status": "error",
                    "message": ("Cannot confirm yet. Please review the order first "
                                "(say 'show my order'), then confirm.")}

        if name == "search_menu_by_allergen":
            last_user = ""
            for m in reversed(self.messages):
                if m.get("role") == "user":
                    last_user = (m.get("content") or "").lower()
                    break
            neg_phrases = ["free", "without", "avoid", "can't eat", "cant eat",
                           "cannot eat", "allergic", "allergy", "no "]
            pos_phrases = ["with", "have", "has ", "contains", "including"]
            if any(p in last_user for p in neg_phrases):
                arguments["contain"] = False
            elif any(p in last_user for p in pos_phrases):
                arguments["contain"] = True

        try:
            return fn(**arguments)
        except TypeError as e:
            return {"status": "error", "message": f"Wrong arguments for {name}: {e}"}
        except Exception as e:
            return {"status": "error", "message": f"Tool {name} crashed: {e}"}

    # -------- Append a tool exchange to conversation history --------
    def _append_tool_exchange(self, user_text: str, tool_calls_made: list[dict],
                              final_reply: str):
        self.messages.append({"role": "user", "content": user_text})

        assistant_tc = []
        for tc in tool_calls_made:
            assistant_tc.append({
                "function": {
                    "name": tc["name"],
                    "arguments": tc.get("arguments", {}),
                }
            })
        self.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": assistant_tc,
        })

        for tc in tool_calls_made:
            self.messages.append({
                "role": "tool",
                "content": json.dumps(tc["result"], ensure_ascii=False),
            })

        self.messages.append({"role": "assistant", "content": final_reply})

    # -------- Intent detection --------
    def _detect_review_intent(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in REVIEW_KEYWORDS)

    def _detect_confirm_intent(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in CONFIRM_KEYWORDS)

    def _detect_add_intent(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in ADD_INTENT_TRIGGERS)

    def _detect_remove_intent(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in REMOVE_INTENT_TRIGGERS)

    def _detect_cancel_order_intent(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in CANCEL_ORDER_TRIGGERS)
    
    def _is_off_topic(self, text: str) -> bool:
        t = text.lower().strip()
        # Whitelist first: any restaurant keyword means NOT off-topic
        if any(k in t for k in RESTAURANT_WHITELIST):
            return False
        # Blocklist: any known off-topic trigger means off-topic
        if any(k in t for k in OFF_TOPIC_TRIGGERS):
            return True
        # Math expression check
        if re.search(r"\bwhat(?:'s|s| is)?\s+\d+\s*[\+\-\*\/x×]\s+\d+", t):
            return True
        return False

    def _handle_off_topic(self, user_text: str):
        if not self._is_off_topic(user_text):
            return None
        final_reply = ("I'm here to help with your Perpetual Bistro order. "
                       "What would you like to eat or drink?")
        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": final_reply})
        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [],
        }

    def _is_question(self, text: str) -> bool:
        t = text.strip().lower()
        if "?" in t:
            return True
        first = t.split()[0] if t.split() else ""
        return first in ("do", "does", "is", "are", "can", "could", "what",
                         "which", "who", "where", "why", "how")

    # -------- Menu item + quantity + note extraction --------
    def _extract_note(self, window: str) -> str:
        """Extract a note phrase from text following an item name."""
        patterns = [
            r'\b(no\s+[a-z][a-z ]{0,30}?)(?=[,\.]|\s+and\s+\d|$)',
            r'\b(without\s+[a-z][a-z ]{0,30}?)(?=[,\.]|\s+and\s+\d|$)',
            r'\b(extra\s+[a-z][a-z ]{0,30}?)(?=[,\.]|\s+and\s+\d|$)',
            r'\b(less\s+[a-z][a-z ]{0,30}?)(?=[,\.]|\s+and\s+\d|$)',
            r'\b(more\s+[a-z][a-z ]{0,30}?)(?=[,\.]|\s+and\s+\d|$)',
            r'\b(on the side)(?=[,\.]|\s+and\s+\d|$)',
            r'\b(with\s+[a-z][a-z ]{0,30}?)(?=[,\.]|\s+and\s+\d|$)',
        ]
        for pat in patterns:
            m = re.search(pat, window, flags=re.IGNORECASE)
            if m:
                note = m.group(1).strip()
                note = re.sub(r'\s+and\s*$', '', note).strip()
                if note:
                    return note
        return ""

    def _extract_items_qty_notes(self, text: str) -> list[dict]:
        """Return [{"name", "qty", "note"}] for each menu item mentioned."""
        all_items = db.get_all_menu().get("items", [])
        text_l = text.lower()

        raw_hits = []
        for item in all_items:
            name = item["name"]
            pos = text_l.find(name.lower())
            if pos == -1:
                continue

            window = text_l[max(0, pos - 20):pos].strip()
            qty = 1
            m = re.search(r'(\d+)\s*(?:x\s*)?$', window)
            if m:
                qty = int(m.group(1))
            else:
                for word, num in NUMBER_WORDS.items():
                    if re.search(rf'\b{word}\s*$', window):
                        qty = num
                        break
            raw_hits.append({
                "pos": pos,
                "name": name,
                "qty": qty,
                "name_end": pos + len(name),
            })

        raw_hits.sort(key=lambda h: h["pos"])
        seen: set[str] = set()
        hits: list[dict] = []
        for h in raw_hits:
            if h["name"] not in seen:
                seen.add(h["name"])
                hits.append(h)

        for i, h in enumerate(hits):
            next_pos = hits[i + 1]["pos"] if i + 1 < len(hits) else len(text)
            window = text[h["name_end"]:next_pos]
            h["note"] = self._extract_note(window)

        return hits

    # -------- Allergen question detection & handling --------
    def _detect_allergen_question(self, text: str):
        t = text.lower()
        allergens = ["gluten", "seafood", "peanuts", "peanut", "dairy",
                     "soy", "fish", "eggs", "egg"]
        found = None
        for a in allergens:
            if re.search(rf"\b{a}\b", t):
                found = a
                break
        if not found:
            return None
        if found == "peanut":
            found = "peanuts"
        elif found == "egg":
            found = "eggs"

        neg = any(re.search(rf"\b{p}", t) for p in
                  ["allergic", "allergy", "free", "without", "avoid", "no"])
        pos = any(re.search(rf"\b{p}", t) for p in
                  ["with", "contain", "have", "has", "want"])

        if neg:
            return (found, False)
        if pos:
            return (found, True)
        return None

    def _handle_allergen_question(self, user_text: str):
        if self.state not in ("STATE_TAKING_ORDER", "STATE_REVIEW"):
            return None
        if not self.session_id:
            return None

        detected = self._detect_allergen_question(user_text)
        if detected is None:
            return None
        allergen, contain = detected

        # Single-item allergen question?
        #   e.g. "Does the Chicken Adobo have dairy?" / "Is Beef Kare-Kare peanut-free?"
        # If exactly one menu item is named, answer about THAT item.
        hits = self._extract_items_qty_notes(user_text)
        if len(hits) == 1:
            item_name = hits[0]["name"]
            item_res = db.get_item_by_name(item_name)
            if item_res.get("status") == "success":
                item = item_res["item"]
                chk = db.check_item_allergen(item["id"], allergen)
                if chk.get("status") == "success":
                    recorded = (", ".join(chk["all_allergens"])
                                if chk["all_allergens"] else "none")
                    if chk["contains"]:
                        if contain:
                            final_reply = (
                                f"Yes, {item['name']} contains {allergen} "
                                f"(recorded allergens: {recorded})."
                            )
                        else:
                            final_reply = (
                                f"No, {item['name']} contains {allergen} "
                                f"(recorded allergens: {recorded}). "
                                f"Please avoid it if you have an allergy."
                            )
                    else:
                        if contain:
                            final_reply = (
                                f"No, {item['name']} does not list {allergen} "
                                f"in its recorded allergens (recorded: {recorded}). "
                                f"Please confirm with staff if the allergy is severe."
                            )
                        else:
                            final_reply = (
                                f"{item['name']} does not list {allergen} "
                                f"in its recorded allergens (recorded: {recorded}). "
                                f"Please confirm with staff if the allergy is severe."
                            )
                    tc_list = [
                        {"name": "get_item_by_name",
                         "arguments": {"item_name": item_name},
                         "result": item_res},
                        {"name": "check_item_allergen",
                         "arguments": {"item_id": item["id"], "allergen": allergen},
                         "result": chk},
                    ]
                    self._append_tool_exchange(user_text, tc_list, final_reply)
                    return {
                        "reply": final_reply,
                        "state": self.state,
                        "session_id": self.session_id,
                        "table_no": self.table_no,
                        "tool_calls": tc_list,
                    }

        # Otherwise: whole-menu scan (existing behavior)

        tool_res = db.search_menu_by_allergen(allergen, contain=contain)
        items = tool_res.get("items", [])
        names = ", ".join(i["name"] for i in items) or "none"

        if contain:
            final_reply = f"Items containing {allergen}: {names}."
        else:
            final_reply = f"Items without {allergen}: {names}."

        self._append_tool_exchange(
            user_text,
            [{"name": "search_menu_by_allergen",
              "arguments": {"allergen": allergen, "contain": contain},
              "result": tool_res}],
            final_reply,
        )

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{"name": "search_menu_by_allergen",
                            "arguments": {"allergen": allergen, "contain": contain},
                            "result": tool_res}],
        }

    # -------- View order (deterministic) --------
    def _format_order_summary(self, order: dict) -> str:
        lines = []
        for ln in order["lines"]:
            note = f" ({ln['instructions']})" if ln["instructions"] else ""
            lines.append(
                f"- {ln['quantity']} x {ln['name']}{note}: ₱{ln['line_total']:.2f}"
            )
        lines_txt = "\n".join(lines)
        return (
            f"Here is your current order at Table {order['table_no']}:\n"
            f"{lines_txt}\n\n"
            f"Subtotal: ₱{order['subtotal']:.2f}\n"
            f"Service Fee (10%): ₱{order['service_fee']:.2f}\n"
            f"Total: ₱{order['grand_total']:.2f}\n\n"
            f"Shall I confirm this order, or would you like to change anything?"
        )

    def _handle_view_order(self, user_text: str):
        if self.state not in ("STATE_TAKING_ORDER", "STATE_REVIEW"):
            return None
        if not self.session_id:
            return None

        wants_review = self._detect_review_intent(user_text)
        wants_confirm = self._detect_confirm_intent(user_text)
        if not (wants_review or wants_confirm):
            return None

        order_res = db.view_current_order(self.session_id)
        if order_res.get("status") != "success":
            return None
        order = order_res["order"]
        if not order["lines"]:
            return None

        if self.state == "STATE_TAKING_ORDER":
            self._set_state("STATE_REVIEW")

        reply = self._format_order_summary(order)
        self._append_tool_exchange(
            user_text,
            [{"name": "view_current_order",
              "arguments": {"session_id": self.session_id},
              "result": order_res}],
            reply,
        )

        return {
            "reply": reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{"name": "view_current_order",
                            "arguments": {"session_id": self.session_id},
                            "result": order_res}],
        }

    # -------- Cancel order (deterministic, with confirmation) --------
    def _handle_cancel_order(self, user_text: str):
        if self.state not in ("STATE_TAKING_ORDER", "STATE_REVIEW"):
            return None
        if not self.session_id:
            return None

        if self._pending_cancel:
            t = user_text.lower().strip()
            yes_words = ["yes", "yeah", "yep", "yup", "sure", "confirm",
                         "ok", "okay", "please do", "do it"]
            no_words = ["no", "nope", "nah", "don't", "dont", "wait",
                        "stop", "keep it", "nevermind", "never mind"]

            is_yes = any(w in t for w in yes_words) and not any(w in t for w in no_words)
            is_no = any(w in t for w in no_words)

            if is_yes:
                self._pending_cancel = False
                res = db.cancel_order(self.session_id)
                if res.get("status") == "success":
                    final_reply = ("Your order has been cancelled. "
                                   "Thank you for visiting Perpetual Bistro.")
                    self._set_state("STATE_COMPLETED")
                else:
                    final_reply = res.get("message", "Could not cancel the order.")
                self._append_tool_exchange(
                    user_text,
                    [{"name": "cancel_order",
                      "arguments": {"session_id": self.session_id},
                      "result": res}],
                    final_reply,
                )
                return {
                    "reply": final_reply,
                    "state": self.state,
                    "session_id": self.session_id,
                    "table_no": self.table_no,
                    "tool_calls": [{"name": "cancel_order",
                                    "arguments": {"session_id": self.session_id},
                                    "result": res}],
                }
            elif is_no:
                self._pending_cancel = False
                final_reply = ("Okay, I won't cancel your order. "
                               "What would you like to do next?")
                self.messages.append({"role": "user", "content": user_text})
                self.messages.append({"role": "assistant", "content": final_reply})
                return {
                    "reply": final_reply,
                    "state": self.state,
                    "session_id": self.session_id,
                    "table_no": self.table_no,
                    "tool_calls": [],
                }
            else:
                # Ambiguous reply; clear the flag and fall through to normal handling
                self._pending_cancel = False
                return None

        if not self._detect_cancel_order_intent(user_text):
            return None

        # First time: ask for confirmation
        self._pending_cancel = True
        final_reply = ("Just to confirm — do you want to cancel your entire order? "
                       "This cannot be undone. Say 'yes' to cancel or 'no' to keep it.")
        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": final_reply})
        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [],
        }
    # -------- Remove item (deterministic) --------
    def _handle_remove_item(self, user_text: str):
        if self.state not in ("STATE_TAKING_ORDER", "STATE_REVIEW"):
            return None
        if not self.session_id:
            return None
        if not self._detect_remove_intent(user_text):
            return None

        hits = self._extract_items_qty_notes(user_text)
        if len(hits) != 1:
            return None
        item_name = hits[0]["name"]

        res = db.remove_item_from_order(self.session_id, item_name)

        if res.get("status") == "success":
            order_res = db.view_current_order(self.session_id)
            remaining = "your order is now empty"
            if order_res.get("status") == "success" and order_res["order"]["lines"]:
                remaining = "your order now has: " + ", ".join(
                    f"{l['quantity']} x {l['name']}"
                    for l in order_res["order"]["lines"]
                )
            reply = f"Removed {item_name} from your order. ({remaining.capitalize()}.)"
        else:
            reply = res.get("message", f"Could not remove {item_name}.")

        self._append_tool_exchange(
            user_text,
            [{"name": "remove_item_from_order",
              "arguments": {"session_id": self.session_id, "item_name": item_name},
              "result": res}],
            reply,
        )

        if res.get("status") == "success" and self.state == "STATE_REVIEW":
            self._set_state("STATE_TAKING_ORDER")

        return {
            "reply": reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{"name": "remove_item_from_order",
                            "arguments": {"session_id": self.session_id,
                                          "item_name": item_name},
                            "result": res}],
        }

    # -------- Change quantity (deterministic) --------
    def _detect_change_qty(self, text: str):
        t = text.strip()
        m = re.search(
            r"\b(?:change|set|update)\s+(?:the\s+)?(.+?)\s+to\s+(\d+)\b",
            t, flags=re.IGNORECASE,
        )
        if m:
            item = m.group(1).strip().strip(" \"'.,!?")
            return (item, int(m.group(2)))
        return None

    def _handle_change_qty(self, user_text: str):
        if self.state not in ("STATE_TAKING_ORDER", "STATE_REVIEW"):
            return None
        if not self.session_id:
            return None

        detected = self._detect_change_qty(user_text)
        if detected is None:
            return None
        item_name, new_qty = detected

        res = db.change_item_quantity(self.session_id, item_name, new_qty)

        if res.get("status") == "success":
            reply = f"Changed {item_name} to {new_qty}. Anything else?"
        else:
            reply = res.get("message", f"Could not update {item_name}.")

        self._append_tool_exchange(
            user_text,
            [{"name": "change_item_quantity",
              "arguments": {"session_id": self.session_id,
                            "item_name": item_name, "new_qty": new_qty},
              "result": res}],
            reply,
        )

        if res.get("status") == "success" and self.state == "STATE_REVIEW":
            self._set_state("STATE_TAKING_ORDER")

        return {
            "reply": reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{"name": "change_item_quantity",
                            "arguments": {"session_id": self.session_id,
                                          "item_name": item_name,
                                          "new_qty": new_qty},
                            "result": res}],
        }

    # -------- Note change (deterministic) --------
    def _detect_note_change(self, text: str):
        t = text.strip()

        m = re.search(
            r"\b(?:change|update|set|replace)\s+the\s+note\s+(?:on|for)\s+"
            r"(.+?)\s+(?:to|with)\s+(.+)$",
            t, flags=re.IGNORECASE,
        )
        if m:
            item = m.group(1).strip()
            note = m.group(2).strip()
            item = re.sub(r"^the\s+", "", item, flags=re.IGNORECASE)
            item = item.strip(" \"'")
            note = note.strip(" \"'")
            if item and note:
                return (item, note)

        m = re.search(
            r"\b(?:change|update|set|replace)\s+(.+?)'s\s+note\s+(?:to|with)\s+(.+)$",
            t, flags=re.IGNORECASE,
        )
        if m:
            item = m.group(1).strip().strip(" \"'")
            note = m.group(2).strip().strip(" \"'")
            if item and note:
                return (item, note)

        return None

    def _handle_note_change(self, user_text: str):
        if self.state not in ("STATE_TAKING_ORDER", "STATE_REVIEW"):
            return None
        if not self.session_id:
            return None

        detected = self._detect_note_change(user_text)
        if detected is None:
            return None
        item_name, new_note = detected

        tool_res = db.update_item_instructions(self.session_id, item_name, new_note)

        if tool_res.get("status") == "success":
            final_reply = f"Updated the note on {item_name} to: {new_note}."
        else:
            final_reply = tool_res.get("message", "Could not update the note.")

        self._append_tool_exchange(
            user_text,
            [{"name": "update_item_instructions",
              "arguments": {"session_id": self.session_id,
                            "item_name": item_name,
                            "new_instructions": new_note},
              "result": tool_res}],
            final_reply,
        )

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{"name": "update_item_instructions",
                            "arguments": {"session_id": self.session_id,
                                          "item_name": item_name,
                                          "new_instructions": new_note},
                            "result": tool_res}],
        }

    # -------- Multi-item add (deterministic) --------
    def _handle_multi_item_add(self, user_text: str):
        if self.state not in ("STATE_TAKING_ORDER", "STATE_REVIEW"):
            return None
        if not self.session_id:
            return None
        if self._is_question(user_text):
            return None
        if not self._detect_add_intent(user_text):
            return None

        hits = self._extract_items_qty_notes(user_text)

        if len(hits) == 0:
            final_reply = ("Sure — which item would you like me to add? "
                           "For example: '1 Chicken Adobo'.")
            self._pending_ambiguous_add = True
            self.messages.append({"role": "user", "content": user_text})
            self.messages.append({"role": "assistant", "content": final_reply})
            return {
                "reply": final_reply,
                "state": self.state,
                "session_id": self.session_id,
                "table_no": self.table_no,
                "tool_calls": [],
            }

        if len(hits) < 2:
            return None

        tool_calls: list[dict] = []
        added: list[str] = []
        errors: list[str] = []
        for h in hits:
            name = h["name"]
            qty = h["qty"]
            note = h.get("note", "")
            res = db.add_item_to_order(self.session_id, name, qty, note)
            tool_calls.append({
                "name": "add_item_to_order",
                "arguments": {"session_id": self.session_id,
                              "item_name": name, "qty": qty,
                              "instructions": note},
                "result": res,
            })
            if res.get("status") == "success":
                label = f"{qty} x {name}"
                if note:
                    label += f" ({note})"
                added.append(label)
            else:
                errors.append(res.get("message", f"Could not add {name}."))

        if added and not errors:
            final_reply = (f"I've added {', '.join(added)} to your order. "
                           f"Anything else?")
        elif added and errors:
            final_reply = (f"I've added {', '.join(added)}. "
                           + " ".join(errors))
        else:
            final_reply = (" ".join(errors) if errors
                           else "I couldn't add those items.")

        self._append_tool_exchange(user_text, tool_calls, final_reply)

        if self.state == "STATE_REVIEW":
            self._set_state("STATE_TAKING_ORDER")

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": tool_calls,
        }

    # -------- Single-item add (deterministic) --------
    def _handle_single_item_add(self, user_text: str):
        if self.state not in ("STATE_TAKING_ORDER", "STATE_REVIEW"):
            self._pending_ambiguous_add = False
            return None
        if not self.session_id:
            self._pending_ambiguous_add = False
            return None
        if self._is_question(user_text):
            self._pending_ambiguous_add = False
            return None

        was_pending = self._pending_ambiguous_add
        has_intent = self._detect_add_intent(user_text)
        if not has_intent and not was_pending:
            return None

        hits = self._extract_items_qty_notes(user_text)
        if len(hits) != 1:
            self._pending_ambiguous_add = False
            return None

        name = hits[0]["name"]
        qty = hits[0]["qty"]
        note = hits[0].get("note", "")

        res = db.add_item_to_order(self.session_id, name, qty, note)
        self._pending_ambiguous_add = False

        if res.get("status") == "success":
            label = f"{qty} x {name}"
            if note:
                label += f" ({note})"
            reply = f"I've added {label} to your order. Anything else?"
        else:
            reply = res.get("message", f"Could not add {name}.")

        self._append_tool_exchange(
            user_text,
            [{"name": "add_item_to_order",
              "arguments": {"session_id": self.session_id,
                            "item_name": name, "qty": qty,
                            "instructions": note},
              "result": res}],
            reply,
        )

        if self.state == "STATE_REVIEW":
            self._set_state("STATE_TAKING_ORDER")

        return {
            "reply": reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{"name": "add_item_to_order",
                            "arguments": {"session_id": self.session_id,
                                          "item_name": name, "qty": qty,
                                          "instructions": note},
                            "result": res}],
        }

    # -------- Deterministic confirm (in REVIEW) --------
    def _handle_confirm_in_review(self, user_text: str):
        if self.state != "STATE_REVIEW" or not self.session_id:
            return None
        if not self._detect_confirm_intent(user_text):
            return None

        order_snapshot = db.view_current_order(self.session_id)
        confirm_res = db.confirm_order(self.session_id)

        if confirm_res.get("status") != "success":
            return None

        self._set_state("STATE_CONFIRMED")

        receipt = confirm_res.get("receipt")
        if not receipt and order_snapshot.get("status") == "success":
            receipt = order_snapshot.get("order")

        total = receipt.get("grand_total", 0) if receipt else 0
        lines_txt = ""
        if receipt and receipt.get("lines"):
            for ln in receipt["lines"]:
                note = f" ({ln['instructions']})" if ln["instructions"] else ""
                lines_txt += (
                    f"\n  - {ln['quantity']} x {ln['name']}{note}: ₱{ln['line_total']:.2f}"
                )
        final_reply = (
            f"Your order has been confirmed! Thank you for dining at Perpetual Bistro."
            f"{lines_txt}\n"
            f"Total: ₱{total:.2f}. Enjoy your meal!"
        )

        self._append_tool_exchange(
            user_text,
            [{"name": "confirm_order",
              "arguments": {"session_id": self.session_id},
              "result": confirm_res}],
            final_reply,
        )

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{"name": "confirm_order",
                            "arguments": {"session_id": self.session_id},
                            "result": confirm_res}],
        }

    # -------- State transitions after tool calls --------
    def _maybe_transition_after_tools(self, tool_calls_made: list[dict]):
        for tc in tool_calls_made:
            name = tc["name"]
            res = tc["result"]
            if name == "create_session" and res.get("status") == "success":
                self.session_id = res["session_id"]
                self.table_no = res["table_no"]
                self._set_state("STATE_TAKING_ORDER")
            elif name == "confirm_order" and res.get("status") == "success":
                self._set_state("STATE_CONFIRMED")
            elif name == "cancel_order" and res.get("status") == "success":
                self._set_state("STATE_COMPLETED")

        modify_tools = {"add_item_to_order", "change_item_quantity",
                        "remove_item_from_order", "clear_session_cart",
                        "update_item_instructions"}
        if self.state == "STATE_REVIEW":
            for tc in tool_calls_made:
                if tc["name"] in modify_tools and tc["result"].get("status") == "success":
                    self._set_state("STATE_TAKING_ORDER")
                    break

    # -------- Main entry --------
    def handle_user_message(self, user_text: str) -> dict:
        if not isinstance(user_text, str) or not user_text.strip():
            return {"reply": "(empty message ignored)", "state": self.state,
                    "tool_calls": [], "session_id": self.session_id}

        user_text = user_text.strip()
        state_at_start = self.state

        if self.state == "STATE_CONFIRMED":
            self._set_state("STATE_COMPLETED")

        # ---------------- GREETING (deterministic) ----------------
        if self.state == "STATE_GREETING" and self.session_id is None:
            m = re.search(r"\b(\d{1,2})\b", user_text)
            if m:
                table_no = int(m.group(1))
                if 1 <= table_no <= 99:
                    res = db.create_session(table_no=table_no)
                    if res.get("status") == "success":
                        self.session_id = res["session_id"]
                        self.table_no = res["table_no"]
                        self._set_state("STATE_TAKING_ORDER")
                        reply = (
                            f"Thanks! Table {table_no} is open. "
                            f"What would you like to order? "
                            f"We have Appetizers, Main Dishes, Rice, Drinks, and Desserts."
                        )
                        self._append_tool_exchange(
                            user_text,
                            [{"name": "create_session",
                              "arguments": {"table_no": table_no},
                              "result": res}],
                            reply,
                        )
                        return {
                            "reply": reply,
                            "state": self.state,
                            "session_id": self.session_id,
                            "table_no": self.table_no,
                            "tool_calls": [{"name": "create_session",
                                            "arguments": {"table_no": table_no},
                                            "result": res}],
                        }
            reply = ("Welcome to Perpetual Bistro! I'm Shelly, your virtual waiter. "
                     "What's your table number?")
            self.messages.append({"role": "user", "content": user_text})
            self.messages.append({"role": "assistant", "content": reply})
            return {"reply": reply, "state": self.state,
                    "session_id": self.session_id, "table_no": self.table_no,
                    "tool_calls": []}

        # ---------------- 1.5. Off-topic refusal (deterministic) ----------------
        off_topic = self._handle_off_topic(user_text)
        if off_topic is not None:
            return off_topic
        
        # ---------------- 1. Deterministic CONFIRM (only if state started in REVIEW) ----
        if state_at_start == "STATE_REVIEW" and self.state == "STATE_REVIEW":
            conf = self._handle_confirm_in_review(user_text)
            if conf is not None:
                return conf

        # ---------------- 2. View order (deterministic) ----------------
        vo = self._handle_view_order(user_text)
        if vo is not None:
            return vo

        # ---------------- 3. Note change (deterministic) ----------------
        note_change = self._handle_note_change(user_text)
        if note_change is not None:
            return note_change

        # ---------------- 4. Allergen question (deterministic) ----------------
        aller = self._handle_allergen_question(user_text)
        if aller is not None:
            return aller

        # ---------------- 4b. Cancel order (deterministic) ----------------
        cancel = self._handle_cancel_order(user_text)
        if cancel is not None:
            return cancel

        # ---------------- 5. Remove item (deterministic) ----------------
        rm = self._handle_remove_item(user_text)
        if rm is not None:
            return rm

        # ---------------- 6. Change qty (deterministic) ----------------
        cq = self._handle_change_qty(user_text)
        if cq is not None:
            return cq

        # ---------------- 7. Multi-item add (deterministic) ----------------
        multi = self._handle_multi_item_add(user_text)
        if multi is not None:
            return multi

        # ---------------- 8. Single-item add (deterministic) ----------------
        single = self._handle_single_item_add(user_text)
        if single is not None:
            return single

        # ---------------- 9. Fall through to LLM (questions, refusals) ----------------
        self.messages.append({"role": "user", "content": user_text})
        all_tool_calls: list[dict] = []
        final_reply = ""

        for _ in range(MAX_TOOL_ITERATIONS):
            data = self._call_ollama()
            msg = data.get("message", {}) or {}
            tool_calls = msg.get("tool_calls") or []

            assistant_msg: dict = {"role": "assistant",
                                   "content": msg.get("content", "") or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.messages.append(assistant_msg)

            if not tool_calls:
                final_reply = (msg.get("content") or "").strip()
                break

            this_round: list[dict] = []
            for tc in tool_calls:
                fn_info = tc.get("function", {})
                name = fn_info.get("name", "")
                args = fn_info.get("arguments", {})
                result = self._execute_tool_call(name, args)
                this_round.append({"name": name, "result": result})
                all_tool_calls.append({"name": name,
                                       "arguments": args, "result": result})
                self.messages.append({
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False),
                })
            self._maybe_transition_after_tools(this_round)

        if not final_reply:
            final_reply = "I'm sorry, I couldn't complete that. Could you rephrase?"

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": all_tool_calls,
        }

    # -------- Introspection --------
    def snapshot(self) -> dict:
        return {"state": self.state, "session_id": self.session_id,
                "table_no": self.table_no, "message_count": len(self.messages)}