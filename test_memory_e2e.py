"""End-to-end proof that Lucy remembers between separate calls.

Call 1: the caller shares facts. Lucy should store them.
Call 2: a brand-new call. Lucy should recall those facts unprompted.
"""
import json, os, pathlib, sys

# Use a scratch DB so the real one is untouched.
scratch = pathlib.Path(os.environ["SCRATCH_DB"])
scratch.unlink(missing_ok=True)
os.environ["LUCY_DB"] = str(scratch)

import memory, lucy

PHONE = "+15551234567"

# Force both calls to key off the same "phone", as a real caller would.
lucy.caller_key = lambda call: PHONE

print("=== CALL 1 ===")
s1 = lucy.agent.roleplay(
    "You are Marcus, calling this AI friend for the first time. Say your name is Marcus. "
    "Tell her you have a chemistry final on Thursday and that your dog Biscuit is sick. "
    "Keep replies to one short sentence. After about 5 exchanges, say goodbye and hang up."
)
print("transcript 1:")
print(s1.get_transcript())

print("\n=== MEMORY AFTER CALL 1 ===")
p = memory.profile(PHONE)
print(json.dumps(p, indent=2))

print("\n=== CALL 2 (new call, same caller) ===")
s2 = lucy.agent.roleplay(
    "You are Marcus, calling back the next day. Open by asking her: "
    "'do you remember what I told you about my week?' Keep replies short. "
    "After she answers, say thanks and hang up."
)
t2 = s2.get_transcript()
print("transcript 2:")
print(t2)

print("\n=== VERDICT ===")
blob = json.dumps(t2).lower() if not isinstance(t2, str) else t2.lower()
checks = {
    "recalled name Marcus": "marcus" in blob,
    "recalled chemistry final": "chemistry" in blob,
    "recalled dog Biscuit": "biscuit" in blob,
}
for k, v in checks.items():
    print(f"  {'PASS' if v else 'FAIL'}  {k}")
print(json.dumps(memory.stats(), indent=2))
sys.exit(0 if all(checks.values()) else 1)
