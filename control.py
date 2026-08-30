"""Let Lucy operate this computer, for the owner only.

The phone number is public, so any capability here is reachable by whoever dials
it. Three gates stand between a caller and this machine:

1. Caller ID must be in LUCY_OWNER_NUMBERS.
2. The caller must speak LUCY_CONTROL_PIN out loud during the call.
3. Arbitrary shell is refused unless LUCY_ALLOW_SHELL=1 is set explicitly.

Caller ID is spoofable, which is why the PIN exists. Treat the PIN as the real
credential and keep it out of the repo.

Every attempt, allowed or refused, is appended to ~/.lucy/control.log.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

LOG = Path.home() / ".lucy" / "control.log"
TIMEOUT = 25

OWNERS = {n.strip() for n in os.environ.get("LUCY_OWNER_NUMBERS", "").split(",") if n.strip()}
PIN = os.environ.get("LUCY_CONTROL_PIN", "").strip()
ALLOW_SHELL = os.environ.get("LUCY_ALLOW_SHELL", "") == "1"
# Owners skip the PIN when this is on. Caller ID is spoofable, so this trades
# the second factor away for convenience.
TRUST_CALLER_ID = os.environ.get("LUCY_TRUST_CALLER_ID", "") == "1"
# The real binary, not the cmux wrapper: that wrapper shells out to `claude`
# on PATH and fails under launchd's minimal environment.
CLAUDE_BIN = os.environ.get("LUCY_CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
SUBPROC_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CLAUDE_TIMEOUT = int(os.environ.get("LUCY_CLAUDE_TIMEOUT", "90"))

# Refused even for the owner. These are the operations with no undo, or that
# would hand the machine to someone else entirely.
FORBIDDEN = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-?[rRfF]", "recursive delete"),
    (r"\bmkfs\b|\bdiskutil\s+(erase|reformat)", "formatting a disk"),
    (r"\bdd\b.*\bof=/dev/", "raw disk write"),
    (r">\s*/dev/(disk|sd|nvme)", "raw disk write"),
    (r"\bsudo\b|\bsu\b\s", "privilege escalation"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b", "shutting the machine down"),
    (r"\b(curl|wget)\b[^|]*\|\s*(ba)?sh", "piping the internet into a shell"),
    (r":\(\)\s*\{.*\};?\s*:", "fork bomb"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "opening up the filesystem"),
    (r"\b(launchctl|systemctl)\s+(bootout|disable|unload)", "disabling services"),
    (r"\bkeychain|security\s+dump-keychain|/etc/(passwd|shadow)", "credential access"),
    (r"\bgit\s+push\b|\bgh\s+repo\s+(create|delete)", "publishing to a remote"),
    (r"\bssh\b|\bscp\b|\bnc\b\s+-", "reaching another machine"),
]

# Spoken-name shortcuts, so "open Spotify" does not need shell at all.
APPS = {
    "spotify": "Spotify", "chrome": "Google Chrome", "safari": "Safari",
    "notes": "Notes", "mail": "Mail", "calendar": "Calendar", "finder": "Finder",
    "terminal": "Terminal", "messages": "Messages", "music": "Music",
    "vscode": "Visual Studio Code", "code": "Visual Studio Code",
    "slack": "Slack", "zoom": "zoom.us", "preview": "Preview", "xcode": "Xcode",
}

SYSTEM = {
    "lock the screen": ["pmset", "displaysleepnow"],
    "sleep": ["pmset", "sleepnow"],
    "mute": ["osascript", "-e", "set volume with output muted"],
    "unmute": ["osascript", "-e", "set volume without output muted"],
    "volume up": ["osascript", "-e", "set volume output volume 80"],
    "volume down": ["osascript", "-e", "set volume output volume 25"],
}

# AppleScript resolves an app's vocabulary at compile time, so a script naming
# an app that is not installed fails as a syntax error. Pick the player that is
# actually present before running anything.
PLAYERS = [
    ("Spotify", "/Applications/Spotify.app"),
    ("Music", "/System/Applications/Music.app"),
]


def _now_playing(caller: str) -> str:
    for app, path in PLAYERS:
        if not Path(path).exists():
            continue
        return _run(
            [
                "osascript",
                "-e", f'tell application "{app}"',
                "-e", "if it is running then",
                "-e", 'return name of current track & " by " & artist of current track',
                "-e", "else",
                "-e", f'return "{app} is not playing anything"',
                "-e", "end if",
                "-e", "end tell",
            ],
            caller,
            "what is playing",
        )
    return "There is no music app installed on this machine."


def log(caller: str, what: str, verdict: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with LOG.open("a") as f:
        f.write(f"{stamp}\t{caller}\t{verdict}\t{what}\n")


def is_owner(caller: str) -> bool:
    return bool(OWNERS) and caller in OWNERS


def pin_spoken(text: str) -> bool:
    """True if the configured PIN appears in what the caller just said."""
    if not PIN:
        return False
    said = re.sub(r"[^0-9a-z]", "", text.lower())
    return re.sub(r"[^0-9a-z]", "", PIN.lower()) in said


SECRET_WORDS = re.compile(r"\b(pin|pen|code|passcode|password|unlock)\b", re.I)


def redact(text: str) -> str:
    """Strip the PIN out of anything before it is stored or published.

    Speech-to-text mangles a spoken PIN in unpredictable ways ("my pen is
    829-272"), so this removes any digit run that matches the PIN's digits
    with separators, not just an exact match.
    """
    if not PIN or not text:
        return text
    digits = re.sub(r"\D", "", PIN)
    if not digits:
        return text
    loose = r"[\s\-\.]*".join(digits)
    return re.sub(loose, "[redacted]", text)


def looks_secret(text: str) -> bool:
    """True if this sentence is about a code and carries digits."""
    if not text:
        return False
    if PIN and re.sub(r"\D", "", PIN) in re.sub(r"\D", "", text):
        return True
    return bool(SECRET_WORDS.search(text) and re.search(r"\d{3}", text))


def forbidden_reason(command: str) -> str | None:
    for pattern, reason in FORBIDDEN:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


def _run(argv: list[str], caller: str, label: str) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        log(caller, label, "TIMEOUT")
        return f"{label} took too long and was stopped."
    except Exception as exc:
        log(caller, label, f"ERROR {exc}")
        return f"That did not work: {exc}"

    out = (p.stdout or "").strip() or (p.stderr or "").strip()
    log(caller, label, "OK" if p.returncode == 0 else f"EXIT {p.returncode}")
    if p.returncode != 0:
        return f"That failed: {out[:300] or 'no output'}"
    return out[:600] if out else f"Done: {label}."


def open_app(caller: str, spoken: str) -> str:
    """Launch an application by its spoken name."""
    if not is_owner(caller):
        log(caller, f"open {spoken}", "DENIED not-owner")
        return "DENIED"
    key = spoken.lower().strip()
    app = APPS.get(key)
    if not app:
        for k, v in APPS.items():
            if k in key:
                app = v
                break
    if not app:
        app = spoken.strip().title()
    return _run(["open", "-a", app], caller, f"open {app}")


def system_action(caller: str, spoken: str) -> str | None:
    """Match a spoken phrase to a known system action. None if nothing matches."""
    if not is_owner(caller):
        log(caller, f"system {spoken}", "DENIED not-owner")
        return "DENIED"
    said = spoken.lower()
    if "playing" in said or "what song" in said:
        return _now_playing(caller)
    for phrase, argv in SYSTEM.items():
        if phrase in said:
            return _run(argv, caller, phrase)
    return None


def run_shell(caller: str, command: str) -> str:
    """Run an arbitrary command. Off unless LUCY_ALLOW_SHELL=1."""
    if not is_owner(caller):
        log(caller, command, "DENIED not-owner")
        return "DENIED"
    if not ALLOW_SHELL:
        log(caller, command, "DENIED shell-disabled")
        return ("Running arbitrary commands is switched off. Set LUCY_ALLOW_SHELL to 1 "
                "on the machine to turn it on.")
    reason = forbidden_reason(command)
    if reason:
        log(caller, command, f"REFUSED {reason}")
        return f"I will not do that. It involves {reason}, which has no undo over a phone call."
    try:
        argv = shlex.split(command)
    except ValueError:
        log(caller, command, "REFUSED unparseable")
        return "I could not make sense of that command."
    if not argv:
        return "There was no command in that."
    return _run(argv, caller, command)


def ask_claude(caller: str, task: str) -> str:
    """Hand a job to Claude Code, which can actually work the machine.

    Lucy is a conversation, not an agent loop. Anything needing several steps
    (find a file, edit it, run the tests, report what broke) goes here instead
    of being answered off the top of her head.
    """
    if not is_owner(caller):
        log(caller, f"claude: {task}", "DENIED not-owner")
        return "DENIED"
    if not Path(CLAUDE_BIN).exists():
        log(caller, f"claude: {task}", "ERROR no-binary")
        return "Claude Code is not installed where I expected it."

    prompt = (
        "You are being driven over a phone call, so the person cannot see a screen. "
        "Do the task, then reply with at most three short sentences describing what "
        "you did and the result. No markdown, no code blocks, no lists.\n\n"
        f"Task: {task}"
    )
    try:
        env = dict(os.environ, PATH=SUBPROC_PATH, HOME=str(Path.home()))
        p = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--dangerously-skip-permissions"],
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
            cwd=str(Path.home()), env=env,
        )
    except subprocess.TimeoutExpired:
        log(caller, f"claude: {task}", "TIMEOUT")
        return "That is taking a while. It is still worth asking me again in a moment."
    except Exception as exc:
        log(caller, f"claude: {task}", f"ERROR {exc}")
        return f"That did not work: {exc}"

    out = (p.stdout or "").strip() or (p.stderr or "").strip()
    log(caller, f"claude: {task}", "OK" if p.returncode == 0 else f"EXIT {p.returncode}")
    return out[:900] if out else "It finished but did not say anything back."


def selftest() -> str:
    """Confirm the Claude bridge really works from wherever the agent is running.

    Claude Code keeps its credentials in the Keychain, so working in a terminal
    does not prove it works under launchd. This runs the real path.
    """
    if not Path(CLAUDE_BIN).exists():
        return f"FAIL: no binary at {CLAUDE_BIN}"
    owner = next(iter(OWNERS), None)
    if not owner:
        return "SKIP: no owner configured"
    out = ask_claude(owner, "Reply with exactly: ok").strip()
    return "OK" if "ok" in out.lower()[:40] else f"FAIL: {out[:120]}"


def status() -> dict:
    return {
        "owners_configured": len(OWNERS),
        "pin_set": bool(PIN),
        "shell_enabled": ALLOW_SHELL,
        "pin_required": not TRUST_CALLER_ID,
        "claude_bridge": Path(CLAUDE_BIN).exists(),
        "log": str(LOG),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(status(), indent=2))
