"""
virtual_waiter_prototype.py - CLI entry point for Shelly the Virtual Waiter.

This is the executable demonstration of the Lesson 6 lab:
  * Runs the FSM agent interactively in the terminal.
  * Logs every turn (user text, state transition, tool calls, reply).
  * On exit, writes sample_conversation_log.json (a required deliverable).

Usage:
    python virtual_waiter_prototype.py
    python virtual_waiter_prototype.py --log path/to/output.json

Commands during the session:
    /quit or /exit  - end the session and save the log
    /state          - print the current FSM state
    /order          - print the current order snapshot from SQLite
    /reset          - wipe the DB and start a brand-new session
"""
from __future__ import annotations
import os
import sys
import json
import argparse
import datetime
import subprocess

# --- Path setup: allow imports from backend/ and database/ ---
HERE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(os.path.dirname(HERE), "database")
LOGS_DIR = os.path.join(os.path.dirname(HERE), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
for p in (HERE, DB_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from fsm_agent import ShellyAgent  # noqa: E402
import db_tools as db  # noqa: E402


BANNER = """
============================================================
  PERPETUAL BISTRO  -  Virtual Waiter (CLI Prototype)
  Powered by Ollama + qwen2.5:3b + SQLite
============================================================
  Commands:  /quit  /state  /order  /reset
============================================================
"""


def _reset_database() -> None:
    """Re-run init_db.py to reset restaurant.db to a clean state."""
    script = os.path.join(DB_DIR, "init_db.py")
    subprocess.run([sys.executable, script], cwd=DB_DIR, check=True)


def _snapshot_order(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    res = db.view_current_order(session_id)
    if res.get("status") == "success":
        return res.get("order")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log", default=os.path.join(LOGS_DIR, "sample_conversation_log.json"),
        help="Path to write the conversation log JSON.",
    )
    parser.add_argument(
        "--no-reset", action="store_true",
        help="Do NOT reset the database on startup.",
    )
    args = parser.parse_args()

    if not args.no_reset:
        print("[init] Resetting restaurant.db ...")
        _reset_database()

    print(BANNER)

    agent = ShellyAgent()
    turns: list[dict] = []
    started_at = datetime.datetime.now().isoformat(timespec="seconds")

    # Emit an initial assistant greeting directly from Python (no user line yet).
    # This matches how a real waiter would greet a table when a session begins.
    # We still need the agent's state machine to drive it, so we push a benign
    # first user message of "hi" which our GREETING shortcut handles
    # without calling any tool.
    boot = agent.handle_user_message("hi")
    print(f"[{boot['state']}] Shelly: {boot['reply']}\n")
    turns.append({
        "turn": 0,
        "user": "(session start)",
        "state_before": "STATE_GREETING",
        "state_after": boot["state"],
        "reply": boot["reply"],
        "tool_calls": boot["tool_calls"],
    })

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[exit] Session ended.")
                break

            if not user_input:
                continue

            if user_input in ("/quit", "/exit"):
                break

            if user_input == "/state":
                print(json.dumps(agent.snapshot(), indent=2))
                continue

            if user_input == "/order":
                snap = _snapshot_order(agent.session_id)
                print(json.dumps(snap, indent=2, ensure_ascii=False))
                continue

            if user_input == "/reset":
                _reset_database()
                agent = ShellyAgent()
                turns = []
                started_at = datetime.datetime.now().isoformat(timespec="seconds")
                print("[reset] Database wiped, new session started.")
                continue

            state_before = agent.state
            result = agent.handle_user_message(user_input)
            print(f"\n[{result['state']}] Shelly: {result['reply']}")
            if result["tool_calls"]:
                print("  (tools called:")
                for tc in result["tool_calls"]:
                    status = tc["result"].get("status")
                    print(f"    - {tc['name']} -> {status}")
                print("  )")
            print()

            turns.append({
                "turn": len(turns),
                "user": user_input,
                "state_before": state_before,
                "state_after": result["state"],
                "reply": result["reply"],
                "tool_calls": [
                    {
                        "name": tc["name"],
                        "arguments": tc.get("arguments"),
                        "result_status": tc["result"].get("status"),
                        "result_message": tc["result"].get("message"),
                    }
                    for tc in result["tool_calls"]
                ],
            })
    finally:
        ended_at = datetime.datetime.now().isoformat(timespec="seconds")
        final_order = _snapshot_order(agent.session_id)

        log = {
            "lab": "Lesson 6 - Conversational State Machines and Workflows",
            "agent": "Shelly",
            "model": "qwen2.5:3b (Ollama)",
            "started_at": started_at,
            "ended_at": ended_at,
            "final_state": agent.state,
            "final_session_id": agent.session_id,
            "final_table_no": agent.table_no,
            "final_order": final_order,
            "turns": turns,
        }

        with open(args.log, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)

        print(f"\n[saved] Conversation log written to: {args.log}")

    return 0


if __name__ == "__main__":
    sys.exit(main())