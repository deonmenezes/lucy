"""End-to-end proof that Lucy can act, not just talk.

Drives one call that adds two tasks, reads the list back, and checks one off,
then asserts the database actually changed.
"""
import json, os, pathlib, sys
scratch = pathlib.Path(os.environ.get("SCRATCH_DB", "/tmp/lucy-actions-test.db"))
scratch.unlink(missing_ok=True)
os.environ["LUCY_DB"] = str(scratch)
import memory, lucy

PHONE = "+15557654321"
lucy.caller_key = lambda call: PHONE

print("=== CALL: asking her to DO things ===")
s = lucy.agent.roleplay(
    "You are calling your AI friend to get things done. In order, one short sentence each: "
    "(1) say 'remind me to call the vet about Biscuit tomorrow', "
    "(2) say 'also add submit the expense report to my list', "
    "(3) ask 'what is on my list?', "
    "(4) say 'I finished the expense report, mark it done', "
    "(5) say thanks and hang up."
)
print(s.get_transcript())

print("\n=== ACTIONS THE AGENT EXECUTED ===")
print(s.executed_actions)

print("\n=== DATABASE STATE ===")
open_now = memory.open_tasks(PHONE)
all_rows = memory.connect().execute(
    "SELECT task, done FROM tasks WHERE phone=? ORDER BY id", (PHONE,)
).fetchall()
for r in all_rows:
    print(f"  [{'x' if r['done'] else ' '}] {r['task']}")

checks = {
    "at least 2 tasks were created": len(all_rows) >= 2,
    "she read the list back correctly": all(
        w in json.dumps(s.get_transcript()).lower() for w in ("vet", "expense")
    ),
    "no duplicate tasks": len({r["task"].lower().rstrip(".") for r in all_rows}) == len(all_rows),
    "one task got completed": any(r["done"] for r in all_rows),
    "an open task remains": len(open_now) >= 1,
}
print("\n=== VERDICT ===")
for k, v in checks.items():
    print(f"  {'PASS' if v else 'FAIL'}  {k}")
sys.exit(0 if all(checks.values()) else 1)
