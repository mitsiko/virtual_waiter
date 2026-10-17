# Shelly Virtual Waiter — Perpetual Bistro

A local, offline conversational restaurant agent. The user talks (via the CLI) to
**Shelly**, a virtual waiter that browses a menu, takes orders, answers questions
about ingredients and allergens, modifies the order, and confirms it. All menu,
price, stock, and order data lives in a local SQLite database.

This project was built for a BS Computer Science AI course (Lab 6: Conversational
State Machines and Workflows). It runs entirely on a laptop — **no cloud APIs**.

---

## 1. Project overview

**What it does.** Shelly is a stateful, multi-turn conversational agent that:

- Maintains session memory across many turns.
- Uses a Finite State Machine (FSM) to enforce conversation flow
  (greeting → ordering → review → confirmed → completed).
- Calls Python tools that read/write SQLite (menu, stock, cart, totals).
- Never invents menu items, prices, or order contents — the database is the
  single source of truth.

**Why it exists.** The lab asked for a virtual waiter that demonstrates:
stateful conversation, function/tool calling with a local LLM, database-backed
order management, and safe handling of edge cases (out-of-stock items,
allergens, invalid inputs, order edits).

**Current scope (as of this README):** the CLI prototype
(`backend/virtual_waiter_prototype.py`). The Web UI, speech-to-text,
text-to-speech, and wake-phrase features are **not** implemented yet — see
section 13.

---

## 2. Current features

Everything below is implemented in the code and has been exercised in real
CLI runs.

### Conversation & session
- Multi-turn memory (the agent keeps the full conversation history).
- FSM states: `STATE_GREETING`, `STATE_TAKING_ORDER`, `STATE_REVIEW`,
  `STATE_CONFIRMED`, `STATE_COMPLETED`.
- Deterministic table-number capture: when the user says a number 1–99 while
  in `STATE_GREETING`, Python opens a session immediately — the LLM is not
  asked to decide this.
- Deterministic REVIEW trigger: phrases like *"show my order"*, *"bill"*,
  *"receipt"*, or any confirm phrase move the FSM to `STATE_REVIEW`.
- Deterministic confirm: if the user is *already* in `STATE_REVIEW` and says
  *"confirm"* / *"finalize"* / *"that's all"*, Python calls `confirm_order`
  directly and produces a fixed receipt-style reply.

### Menu & information
- Browse menu by category (`get_menu_by_category`).
- Look up a single item by name (`get_item_by_name`) including description,
  ingredients, price, stock, allergens.
- Check whether a specific item contains a specific allergen
  (`check_item_allergen`).
- Scan the whole menu for items that *do* or *do not* contain an allergen
  (`search_menu_by_allergen`). A Python pre-parser reads the user's phrasing
  ("allergic to X", "X-free", "with X") and forces the correct `contain`
  polarity so small models cannot flip it.

### Order management
- Add an item with quantity and optional note (`add_item_to_order`).
- Multi-item add in one message (e.g. *"Add 1 Chicken Adobo and 2 Iced Tea"*)
  is handled by a Python pre-parser that calls `add_item_to_order` once per
  item. The LLM only writes the summary sentence.
- Change quantity (`change_item_quantity`).
- Change the note on an existing item (`update_item_instructions`). The
  previous note is **replaced**, not appended.
- Remove an item (`remove_item_from_order`).
- View current order with itemized lines, subtotal, 10% service fee, and
  grand total (`view_current_order`).
- Confirm the order (`confirm_order`) — deducts stock, locks the session.

### Special instructions
- "No onions", "extra sauce", "requested vegetarian" etc. are stored as
  free-text notes on the line item.
- A Python pre-parser detects *"change the note on X to Y"* and *"change X's
  note to Y"* and calls `update_item_instructions` directly. This is
  necessary because the small model confuses "make it vegetarian"
  (a recipe change — refused) with "change the note to 'requested
  vegetarian'" (a note change — allowed).

### Guardrails
- **Recipe changes are refused.** *"Make it vegetarian"*, *"add extra
  cheese"*, etc. get a polite refusal plus an offer to store the request as a
  note instead. The code never claims the recipe was changed.
- **Off-topic questions are refused.** The persona instructs the LLM to
  reply with a fixed sentence for anything that isn't about Perpetual Bistro.
- **Confirmation is blocked outside REVIEW.** Even if the LLM tries, the
  Python tool executor returns an error unless the FSM is in `STATE_REVIEW`.
- **Tool arguments are validated in Python.** Unknown items, negative or
  oversized quantities, sessions that are already CONFIRMED/CANCELLED, and
  empty note strings are all rejected before anything is written.

### Logging
- Every CLI session writes `logs/sample_conversation_log.json` containing
  each turn's user text, state before/after, reply, and tool calls.

---

## 3. Technology stack and prerequisites

| Component | Version / Notes |
|-----------|-----------------|
| Python | 3.10 or newer (developed against 3.12) |
| Ollama | Latest stable — running locally |
| LLM model | **`qwen2.5:3b`** (verified to support Ollama's `tools` parameter) |
| SQLite | Bundled with Python (`sqlite3` module) |
| Python deps | `requests`, `flask` (Flask is not needed for the CLI, but is present in `requirements.txt`) |
| OS | Developed on Windows 11; code is cross-platform |

The model is the only LLM in use. No paid APIs. No cloud calls.

---

## 4. Installation and setup (Windows)

Run these commands in **PowerShell** from the project root
(`shelly-virtual-waiter`).

### 4.1 Verify Python

```powershell
python --version
```

Expect Python 3.10+. If `python` maps to a non-Windows interpreter (e.g.
MSYS2), use the full path to the Windows Python executable for the next step.

### 4.2 Create and activate a virtual environment

```powershell
& "C:\Path\To\Python312\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
```

You must see `(.venv)` at the start of your terminal prompt for every command
below.

If PowerShell blocks the activation script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Then activate again.

### 4.3 Install dependencies

```powershell
pip install -r requirements.txt
```

If `requirements.txt` is missing, run:

```powershell
pip install requests flask
```

### 4.4 Verify Ollama

```powershell
ollama --version
ollama list
```

`qwen2.5:3b` must appear in the list. If it does not:

```powershell
ollama pull qwen2.5:3b
```

Make sure the Ollama server is running (it usually runs as a background
service; `ollama serve` starts it manually if needed).

### 4.5 Prepare the database

The database is created from `database/init_restaurant_db.sql`.

```powershell
cd database
python init_db.py
```

Expected output:

```
[OK] Database created: ...\database\restaurant.db
[OK] Menu items inserted: 16
     - Appetizers: 3 items
     ...
[OK] Out-of-stock items (for testing): ['Vegetable Spring Rolls', 'Grilled Bangus']
```

Then return to the project root:

```powershell
cd ..
```

**Warning:** re-running `init_db.py` resets the database and deletes every
open session and cart. Only run it when you want a fresh state.

---

## 5. How to run the project

### 5.1 Start the CLI

From the project root, with the venv active:

```powershell
cd backend
python virtual_waiter_prototype.py
```

You will see a banner and the first greeting.

### 5.2 Interact

At the `You:` prompt, type messages and press Enter. Suggested opening:

```
Hi
Table 5
What appetizers do you have?
Add 1 Chicken Adobo, no onions
Show my order
Confirm my order
```

### 5.3 CLI commands

| Command | Meaning |
|---------|---------|
| `/quit` or `/exit` | End the session and save the log |
| `/state` | Print the current FSM state as JSON |
| `/order` | Print the current order snapshot from SQLite |
| `/reset` | Reset the database and start a new session |

### 5.4 Where the log goes

After `/quit`, the log is written to
`logs/sample_conversation_log.json`.

---

## 6. Project structure

```
shelly-virtual-waiter/
├── README.md                          # this file
├── requirements.txt                   # pip dependencies
├── backend/
│   ├── fsm_agent.py                   # FSM controller + Ollama client
│   ├── virtual_waiter_prototype.py    # CLI entry point, writes the JSON log
│   ├── test_fsm_agent.py              # interactive CLI smoke test
│   └── check_note_change.py           # tiny script that tests the note regex
├── database/
│   ├── init_restaurant_db.sql         # schema + 16 seed rows
│   ├── init_db.py                     # runs the SQL file and creates restaurant.db
│   ├── db_tools.py                    # all tool functions + Ollama tool schemas
│   ├── restaurant.db                  # SQLite database (generated — safe to delete)
│   └── test_db_tools.py               # scripted test of every tool
├── logs/
│   └── sample_conversation_log.json   # written by the CLI on /quit
├── frontend/                          # EMPTY — reserved for the future Web UI
└── docs/                              # EMPTY — reserved for future docs
```

**Key files to open first:**

- `database/db_tools.py` — every tool and its schema.
- `backend/fsm_agent.py` — the FSM and the Ollama loop.
- `backend/virtual_waiter_prototype.py` — the CLI shell and log writer.

---

## 7. Architecture and workflow

```
┌──────────────────────┐
│  Terminal (user)     │
│  types a message     │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐    Deterministic pre-parsers:
│  ShellyAgent          │    • greeting / table number
│  (fsm_agent.py)       │    • review / confirm intent
│                       │    • allergen scan
│                       │    • multi-item add
│                       │    • note change
└─────┬──────────┬──────┘
      │          │
      │          └───────► (sometimes answers directly, no LLM)
      │
      ▼
┌──────────────────────┐
│  Ollama /api/chat    │  model: qwen2.5:3b
│  with `tools=[...]`  │  receives message history + tool schemas
└─────┬────────────────┘
      │ model emits `tool_calls` or plain text
      ▼
┌──────────────────────┐
│  Tool dispatcher     │  validates arguments, injects the real
│  (fsm_agent.py)      │  session_id, enforces confirm-only-in-REVIEW
└─────┬────────────────┘
      │ calls one of 16 functions
      ▼
┌──────────────────────┐
│  db_tools.py         │  pure Python; every read/write hits SQLite
└─────┬────────────────┘
      │
      ▼
┌──────────────────────┐
│  restaurant.db       │  single source of truth
└──────────────────────┘
```

### Flow of a single turn

1. `virtual_waiter_prototype.py` reads a line and calls
   `ShellyAgent.handle_user_message(text)`.
2. Python shortcut checks run first (greeting, review, confirm, allergen,
   multi-item add, note change). If any of them produces an answer, the LLM
   is skipped for that turn.
3. Otherwise, the message is appended to `self.messages` and sent to Ollama.
4. If the model replies with `tool_calls`, each call is executed by
   `_execute_tool_call`. Results are appended to the message history with
   `role="tool"` so the model can see them on the next iteration.
5. This loop repeats up to `MAX_TOOL_ITERATIONS = 6` times or until the model
   emits a plain-text reply.
6. The reply, the current FSM state, the session id, and the list of tool
   calls are returned to the CLI. The CLI prints them and appends a turn to
   the in-memory log.

---

## 8. Database and menu

### 8.1 Schema

Three tables:

```
menu_items
    id, name UNIQUE, category, description, ingredients,
    price REAL, stock_qty INTEGER, allergens TEXT

table_sessions
    session_id TEXT PRIMARY KEY, table_no INTEGER,
    status TEXT CHECK(OPEN|REVIEWING|CONFIRMED|CANCELLED),
    created_at, confirmed_at

order_line_items
    line_id, session_id, item_id, quantity,
    special_instructions, added_at
```

`order_line_items` cascades on `table_sessions` deletion.

### 8.2 Menu

16 items across 5 categories:

- **Appetizers** (3) — Crispy Lumpia Shanghai, Calamari Fritters,
  Vegetable Spring Rolls *(out of stock at seed time)*
- **Main Dishes** (5) — Chicken Adobo, Beef Kare-Kare, Pork Sinigang,
  Grilled Bangus *(out of stock at seed time)*, Vegetable Pad Thai
- **Rice** (2) — Steamed White Rice, Garlic Fried Rice
- **Drinks** (3) — Iced Tea, Fresh Buko Juice, Calamansi Juice
- **Desserts** (3) — Halo-Halo, Leche Flan, Mango Graham *(low stock: 2)*

Prices are in Philippine pesos.

### 8.3 Business rules implemented in code

| Rule | Where |
|------|-------|
| Service fee = 10% of subtotal | `SERVICE_FEE_RATE` in `db_tools.py` |
| Maximum quantity per line = 20 | `MAX_QTY_PER_LINE` in `db_tools.py` |
| Stock deduction happens at confirm time, not at add time | `confirm_order()` |
| Adding never exceeds remaining stock | `add_item_to_order()` |
| Notes are free-text, stored on the line item | `special_instructions` column |
| Same item + same note merges into one line | `add_item_to_order()` |
| Same item + different note creates a second line | `add_item_to_order()` |
| Sessions become `REVIEWING` once the first item is added | `add_item_to_order()` |
| Confirmed sessions are locked; further edits are refused | `_validate_session_open()` |
| Stock is re-checked at confirm time | `confirm_order()` |

### 8.4 Totals

```
subtotal      = Σ (item.price × line.quantity)
service_fee   = round(subtotal × 0.10, 2)
grand_total   = round(subtotal + service_fee, 2)
```

---

## 9. Strengths and limitations

### Strengths
- Menu, prices, stock, and allergens come from SQLite — no hallucination.
- Deterministic shortcuts cover the intents that qwen2.5:3b is unreliable
  on: table numbers, multi-item adds, allergen scans, note changes, and
  confirm-once-in-REVIEW.
- Tool arguments are validated before any write.
- The confirm guard prevents "confirmed" language before the FSM is in
  REVIEW.
- The JSON log is honest about what tools were actually called.

### Known limitations
- **Model size.** qwen2.5:3b is small. It sometimes paraphrases awkwardly,
  emits `$` instead of `₱` despite instructions, or produces redundant
  sentences.
- **Note-change coverage is narrow.** Only the two regex patterns in
  `_detect_note_change` are handled deterministically. Other phrasings
  fall through to the LLM and may fail.
- **Multi-item parse coverage.** The pre-parser detects item names by
  substring matching on the menu. If a user says something that looks like
  an item name for an unrelated reason, it can misfire.
- **No authentication, no concurrency.** The CLI is single-user. The Web UI
  (future) will hold one agent in memory.
- **No refund / undo after confirmation.** Once confirmed, the session is
  locked.
- **No clear-note command in the CLI.** Clearing a note is deferred to the
  Web UI.
- **Log timestamps are local naive datetimes.** No time zone handling.

---

## 10. Testing and verification

### Actually-run tests

The following scenarios have been exercised in real CLI runs during
development and were verified by inspecting the transcripts:

| # | Scenario | Result |
|---|----------|--------|
| T1 | Database init | `[OK] Menu items inserted: 16` |
| T2 | Stock deduction after confirm | Chicken Adobo 25→23, Iced Tea 50→47 |
| T3 | Out-of-stock refusal with alternatives | Vegetable Spring Rolls rejected |
| T4 | Invalid quantity (0, -5, 999) | Rejected by `_validate_qty` |
| T5 | Allergen scan polarity | "anything with peanuts" → items WITH peanuts |
| T6 | Multi-item single message | 2 items → 2 tool calls, both success |
| T7 | Unsupported customization | "make it vegetarian" refused politely |
| T8 | Note change — explicit phrasing | `update_item_instructions` success |
| T9 | Confirm flow via REVIEW | First `confirm` moves to REVIEW, second finalizes |
| T10 | Off-topic guardrail | "capital of France" → fixed refusal |

### Recommended (not yet systematically run)

- Fuzz the note-change regex with typos and mixed case.
- Add 2 different notes to the same item and verify two lines.
- Interrupt mid-order by asking an off-topic question, then resume.
- Confirm guard: verify "confirm" from `STATE_TAKING_ORDER` does not
  finalize.
- Kill Ollama mid-session — should raise a clear error.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ModuleNotFoundError: requests` | venv not active, or installed into a different Python | Activate `.venv`, then `pip install -r requirements.txt` |
| `Cannot reach Ollama at http://localhost:11434` | Ollama server not running | Run `ollama serve` in another terminal |
| `model "qwen2.5:3b" not found` | Model not pulled | `ollama pull qwen2.5:3b` |
| `Session 'T5-S001' not found` | Fresh DB after code ran | Re-run `python init_db.py` from `database/` |
| `Tool 'x' is not registered` | Tool missing from `TOOL_REGISTRY` | Add the function to `TOOL_REGISTRY` in `db_tools.py` |
| Shelly calls the wrong tool | Small model confusion | Add or extend a Python pre-parser in `fsm_agent.py` |
| Shelly says "$" instead of "₱" | LLM style drift | Strengthen the persona; consider wrapping the reply |
| `TypeError: handle_user_message() missing 1 required positional argument` | Calling the method without a string | Pass the user text as a string |
| `sqlite3.OperationalError: no such table` | DB file missing or wrong path | Run `init_db.py`; check `DB_PATH` in `db_tools.py` |

---

## 12. Developer handoff and extension guide

### Where to look first
- **Behavior is wrong for a specific phrase** → `backend/fsm_agent.py`,
  the `_handle_*` pre-parsers.
- **A tool call is rejected or malformed** → `database/db_tools.py`,
  the tool function itself, and `_execute_tool_call` in `fsm_agent.py`.
- **Menu / price / stock is wrong** → `database/init_restaurant_db.sql`
  and `init_db.py`.
- **Persona style is wrong** → `BASE_PERSONA` in `fsm_agent.py`.

### How to add a new tool

1. Add a function to `database/db_tools.py`:
   ```python
   def my_new_tool(arg1: str, arg2: int) -> dict:
       # validate args, hit SQLite, return {"status": "success"|"error", ...}
   ```
2. Add a schema entry to `TOOL_SCHEMAS` in the same file, matching the
   function's arguments.
3. Add the mapping to `TOOL_REGISTRY`:
   ```python
   "my_new_tool": my_new_tool,
   ```
4. If the new tool modifies the cart, add its name to the `modify_tools`
   set in `_maybe_transition_after_tools` in `fsm_agent.py` so REVIEW
   transitions back to TAKING_ORDER.
5. If the new tool needs `session_id` and the model might omit it, add
   its name to the `session_tools` set in `_execute_tool_call`.
6. Update `BASE_PERSONA` with a short rule describing when to call it.
7. Test directly first:
   ```python
   import db_tools; print(db_tools.my_new_tool("x", 1))
   ```
8. Then test through the CLI.

### Preserving state / SQLite sync

- Never write the cart or menu directly from the LLM path. Always go
  through a tool function in `db_tools.py`.
- Never read from the model's memory — read from
  `view_current_order()` or `get_item_by_name()`.
- After every successful cart-modifying tool, the FSM must move back to
  `STATE_TAKING_ORDER` if it was in `STATE_REVIEW`.

### Testing changes safely

1. Copy `logs/sample_conversation_log.json` aside before re-running.
2. Reset the DB only if the change requires it:
   `cd database && python init_db.py`.
3. Run `python test_db_tools.py` to smoke-test the tools directly.
4. Run `python virtual_waiter_prototype.py` and paste the transcript back
   for review.

---

## 13. Future work

The following are **planned or discussed but not implemented**:

- **Web UI** (planned, `frontend/` is empty). A Flask backend + HTML/CSS/JS
  frontend that mirrors the CLI: chat panel on the left, live order panel
  on the right, fixed input bar with a microphone button at the bottom.
  The Flask server will hold one `ShellyAgent` in memory and serve
  `/api/chat`, `/api/order`, and menu endpoints. The order panel is planned
  to poll `/api/order` every few seconds.
- **Speech-to-text (STT)** — the browser Web Speech API is the simplest
  path, but it requires network access on most browsers. An offline
  alternative (Vosk, faster-whisper) is possible but not chosen yet.
- **Text-to-speech (TTS)** — the browser SpeechSynthesis API is the
  simplest offline path on Windows.
- **Wake phrase "Hey, Shelly"** — this requires a dedicated engine
  (openWakeWord or Porcupine). Not implemented. The trade-offs are not
  resolved yet.
- **Clear-note command** — the `update_item_instructions` tool currently
  rejects empty strings. A dedicated Web UI "Clear note" action should use
  a separate tool rather than loosening this guard.
- **More robust note-change parsing** — extend `_detect_note_change` with
  additional patterns as users surface them.
- **Time zone handling** in the JSON log.

### What can be reused

The entire `backend/fsm_agent.py` and `database/db_tools.py` layer can be
reused unchanged by the Web UI. The Flask server only needs to wrap
`ShellyAgent` behind HTTP endpoints. Nothing in the FSM assumes a
terminal.

---

## Verification status (what this README could not fully confirm)

The following items were written from the code that was supplied and run
during development, not from a fresh inspection of every file in the
repository at the time of writing:

- The exact contents of `backend/virtual_waiter_prototype.py`,
  `backend/test_fsm_agent.py`, `backend/check_note_change.py`,
  `database/init_db.py`, `database/init_restaurant_db.sql`, and
  `database/test_db_tools.py` were **not** re-read from the current
  filesystem. The behavior documented here matches what those scripts
  have produced in the transcripts shared during development.
- The `requirements.txt` file is documented as containing `flask>=3.0`
  and `requests>=2.31`. If it is missing or contains different content,
  update section 3 and 4.3 accordingly.
- The `frontend/` and `docs/` folders are empty. If anything has been
  added since, update section 6.

Nothing else in this README is speculative. Anything labeled "planned"
in section 13 is genuinely not implemented.
