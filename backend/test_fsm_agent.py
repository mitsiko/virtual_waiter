"""Interactive CLI test for the FSM agent.
Run from /backend:  python test_fsm_agent.py
Type 'quit' to exit. Type 'state' to print current FSM state.
"""
import os, sys, json, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(os.path.dirname(HERE), "database")
sys.path.insert(0, HERE)
sys.path.insert(0, DB_DIR)

# Reset DB before testing
subprocess.run([sys.executable, "init_db.py"], cwd=DB_DIR, check=True)

from fsm_agent import ShellyAgent

def main():
    print("\n=== Shelly FSM Agent Test ===")
    print("Type 'quit' to exit, 'state' to show FSM state.\n")
    agent = ShellyAgent()
    # Print greeting directly by triggering an empty-but-meaningful first user turn
    first = agent.handle_user_message("Hi")
    print(f"[{agent.state}] Shelly: {first['reply']}\n")

    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not user:
            continue
        if user.lower() == "quit":
            break
        if user.lower() == "state":
            print(json.dumps(agent.snapshot(), indent=2))
            continue

        result = agent.handle_user_message(user)
        print(f"\n[{result['state']}] Shelly: {result['reply']}")
        if result["tool_calls"]:
            print("  (tools called:")
            for tc in result["tool_calls"]:
                status = tc["result"].get("status")
                print(f"    - {tc['name']} -> {status}")
            print("  )")
        print()

if __name__ == "__main__":
    main()