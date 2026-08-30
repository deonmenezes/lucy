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


def status() -> dict:
    return {
        "owners_configured": len(OWNERS),
        "pin_set": bool(PIN),
        "shell_enabled": ALLOW_SHELL,
        "log": str(LOG),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(status(), indent=2))
