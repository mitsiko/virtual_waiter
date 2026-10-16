import fsm_agent

a = fsm_agent.ShellyAgent()
print("has _handle_note_change:", hasattr(a, "_handle_note_change"))
print("has _detect_note_change:", hasattr(a, "_detect_note_change"))
print("detect:", a._detect_note_change("Change the note on the Chicken Adobo to extra sauce"))
print("detect:", a._detect_note_change("Change the note on the Chicken Adobo to requested vegetarian"))
print("detect:", a._detect_note_change("Make the Chicken Adobo vegetarian"))