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

        # Always trust our own session_id: override whatever the LLM passed.
        session_tools = {"add_item_to_order", "view_current_order",
                         "change_item_quantity", "remove_item_from_order",
                         "calculate_totals", "confirm_order", "cancel_order"}
        if name in session_tools and self.session_id:
            arguments["session_id"] = self.session_id

        if name == "confirm_order" and self.state != "STATE_REVIEW":
            return {"status": "error",
                    "message": ("Cannot confirm yet. Please review the order first "
                                "(say 'show my order'), then confirm.")}

        # Deterministic contain override for allergen scans.
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

    # -------- Allergen question detection & handling --------
    def _detect_allergen_question(self, text: str):
        """Returns (allergen, contain_bool) or None."""
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
        if self.state != "STATE_TAKING_ORDER" or not self.session_id:
            return None

        detected = self._detect_allergen_question(user_text)
        if detected is None:
            return None
        allergen, contain = detected

        tool_res = db.search_menu_by_allergen(allergen, contain=contain)

        summary_instr = (
            f"The system just executed search_menu_by_allergen with "
            f"allergen='{allergen}', contain={contain}. The tool returned: "
            f"{json.dumps(tool_res, ensure_ascii=False)}. "
            f"You are Shelly speaking to the customer in first person. Reply in "
            f"1-2 short sentences. If contain=True, list the items that CONTAIN "
            f"{allergen}. If contain=False, list the items that DO NOT contain "
            f"{allergen}. Do NOT call any more tools."
        )

        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "system", "content": summary_instr})

        try:
            data = self._call_ollama()
            msg = data.get("message", {}) or {}
            final_reply = (msg.get("content") or "").strip()
        except Exception:
            final_reply = ""

        if not final_reply:
            items = tool_res.get("items", [])
            names = ", ".join(i["name"] for i in items) or "none"
            if contain:
                final_reply = f"Items containing {allergen}: {names}."
            else:
                final_reply = f"Items without {allergen}: {names}."

        self.messages.append({"role": "assistant", "content": final_reply})

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{
                "name": "search_menu_by_allergen",
                "arguments": {"allergen": allergen, "contain": contain},
                "result": tool_res,
            }],
        }

    # -------- Multi-item pre-parser --------
    def _extract_items_and_qty(self, text: str) -> list[tuple[str, int]]:
        all_items = db.get_all_menu().get("items", [])
        text_l = text.lower()

        hits: list[tuple[int, str, int]] = []
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
            hits.append((pos, name, qty))

        hits.sort(key=lambda h: h[0])
        seen: set[str] = set()
        out: list[tuple[str, int]] = []
        for _, name, qty in hits:
            if name not in seen:
                seen.add(name)
                out.append((name, qty))
        return out

    def _is_question(self, text: str) -> bool:
        t = text.strip().lower()
        if "?" in t:
            return True
        first = t.split()[0] if t.split() else ""
        return first in ("do", "does", "is", "are", "can", "could", "what",
                         "which", "who", "where", "why", "how")

    def _handle_multi_item_add(self, user_text: str):
        if self.state != "STATE_TAKING_ORDER" or not self.session_id:
            return None
        if self._is_question(user_text):
            return None
        if not self._detect_add_intent(user_text):
            return None

        pairs = self._extract_items_and_qty(user_text)
        if len(pairs) < 2:
            return None

        tool_calls: list[dict] = []
        for name, qty in pairs:
            res = db.add_item_to_order(self.session_id, name, qty)
            tool_calls.append({
                "name": "add_item_to_order",
                "arguments": {"session_id": self.session_id,
                              "item_name": name, "qty": qty},
                "result": res,
            })

        summary_instr = (
            "You are Shelly speaking to the customer in first person. "
            "The system has just added these items to their order on your behalf. "
            "Reply with ONE short sentence confirming what was added (mention each "
            "item and its quantity), then ask if they'd like anything else. "
            "Say 'I've added...' NOT 'The customer added...'. "
            "Do NOT call any tools.\n\n"
            "Added:\n" + json.dumps(
                [{"item": tc["arguments"]["item_name"],
                  "qty": tc["arguments"]["qty"],
                  "status": tc["result"].get("status"),
                  "message": tc["result"].get("message")}
                 for tc in tool_calls],
                ensure_ascii=False, indent=2)
        )

        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "system", "content": summary_instr})

        try:
            data = self._call_ollama()
            msg = data.get("message", {}) or {}
            final_reply = (msg.get("content") or "").strip()
        except Exception:
            final_reply = ""

        if not final_reply:
            names = ", ".join(f"{q} x {n}" for n, q in pairs)
            final_reply = f"I've added {names} to your order. Anything else?"

        self.messages.append({"role": "assistant", "content": final_reply})

        if self.state == "STATE_REVIEW":
            self._set_state("STATE_TAKING_ORDER")

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": tool_calls,
        }

        # -------- Note change pre-parser --------
    def _detect_note_change(self, text: str):
        """Return (item_name, new_instructions) if the user is asking to
        change the note on an existing item. Otherwise return None."""
        t = text.strip()

        # Pattern 1: <verb> the note on/for <item> to/with <note>
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

        # Pattern 2: <verb> <item>'s note to/with <note>
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

        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": final_reply})

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{
                "name": "update_item_instructions",
                "arguments": {"session_id": self.session_id,
                              "item_name": item_name,
                              "new_instructions": new_note},
                "result": tool_res,
            }],
        }
    
# -------- Deterministic confirm when already in REVIEW --------
    def _handle_confirm_in_review(self, user_text: str):
        """Only fires if the FSM was ALREADY in REVIEW at the start of the turn
        and the user expressed a confirm intent."""
        if self.state != "STATE_REVIEW" or not self.session_id:
            return None
        if not self._detect_confirm_intent(user_text):
            return None

        order_snapshot = db.view_current_order(self.session_id)
        confirm_res = db.confirm_order(self.session_id)

        if confirm_res.get("status") != "success":
            # Fall through to the LLM so it can explain the error
            return None

        self._set_state("STATE_CONFIRMED")

        receipt = confirm_res.get("receipt")
        if not receipt and order_snapshot.get("status") == "success":
            receipt = order_snapshot.get("order")

        # Deterministic reply - the receipt is exact, no LLM needed.
        total = receipt.get("grand_total", 0) if receipt else 0
        lines_txt = ""
        if receipt and receipt.get("lines"):
            for ln in receipt["lines"]:
                lines_txt += (
                    f"\n  - {ln['quantity']} x {ln['name']}: ₱{ln['line_total']:.2f}"
                )
        final_reply = (
            f"Your order has been confirmed! Thank you for dining at Perpetual Bistro."
            f"{lines_txt}\n"
            f"Total: ₱{total:.2f}. Enjoy your meal!"
        )

        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": final_reply})

        return {
            "reply": final_reply,
            "state": self.state,
            "session_id": self.session_id,
            "table_no": self.table_no,
            "tool_calls": [{
                "name": "confirm_order",
                "arguments": {"session_id": self.session_id},
                "result": confirm_res,
            }],
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
                        "remove_item_from_order", "clear_session_cart"}
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

        # Confirmed -> COMPLETED on the next turn
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
                        self.messages.append({"role": "user", "content": user_text})
                        self.messages.append({"role": "assistant", "content": reply})
                        return {
                            "reply": reply,
                            "state": self.state,
                            "session_id": self.session_id,
                            "table_no": self.table_no,
                            "tool_calls": [{
                                "name": "create_session",
                                "arguments": {"table_no": table_no},
                                "result": res,
                            }],
                        }
            reply = ("Welcome to Perpetual Bistro! I'm Shelly, your virtual waiter. "
                     "What's your table number?")
            self.messages.append({"role": "user", "content": user_text})
            self.messages.append({"role": "assistant", "content": reply})
            return {"reply": reply, "state": self.state,
                    "session_id": self.session_id, "table_no": self.table_no,
                    "tool_calls": []}

        # ---------------- Deterministic REVIEW trigger ----------------
        if self.state == "STATE_TAKING_ORDER" and self.session_id:
            wants_review = self._detect_review_intent(user_text)
            wants_confirm = self._detect_confirm_intent(user_text)
            if wants_review or wants_confirm:
                order = db.view_current_order(self.session_id)
                if order.get("status") == "success" and order["order"]["lines"]:
                    self._set_state("STATE_REVIEW")

        # ---------------- Deterministic CONFIRM (only if already in REVIEW) --------
        # Runs only when the user was ALREADY in REVIEW when the turn began,
        # so a fresh "confirm" from TAKING_ORDER still lets them see the order first.
        if state_at_start == "STATE_REVIEW" and self.state == "STATE_REVIEW":
            conf = self._handle_confirm_in_review(user_text)
            if conf is not None:
                return conf

        # ---------------- Note change pre-parser ----------------
        note_change = self._handle_note_change(user_text)
        if note_change is not None:
            return note_change

        # ---------------- Allergen question pre-parser ----------------
        aller = self._handle_allergen_question(user_text)
        if aller is not None:
            return aller

        # ---------------- Multi-item add pre-parser ----------------
        multi = self._handle_multi_item_add(user_text)
        if multi is not None:
            return multi

        # ---------------- Normal LLM turn ----------------
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