# Lucy - an AI friend with memory

Lucy answers **+1 (484) 990-1621**, talks about anything, and remembers every
call. Next time you ring, she already knows who you are and what you were
dealing with.

Built on the [Guava](https://goguava.ai) voice SDK. Runs locally, no VM.

## How the memory works

Everything is a single SQLite file at `~/.lucy/memory.db`. No vector database,
no third-party API keys.

| Table | Holds |
|---|---|
| `callers` | one row per phone number, plus the name she learned |
| `turns` | every utterance, written **live** so a crash mid-call loses nothing |
| `facts` | durable extracted facts (people, plans, ongoing situations) |
| `calls` | per-call summary |
| `search` | FTS5 index over turns, facts, and summaries |

The lifecycle, in `lucy.py`:

- **`on_call_start`** loads the caller's profile and hands it to Lucy with
  `call.add_info` before she speaks, so her opening line can reference last time.
- **`on_caller_speech` / `on_agent_speech`** append each turn to disk immediately.
- **`on_question`** runs an FTS5 search across everything she has ever stored
  for that caller, then answers only from what it returns.
- **`on_session_end`** distills the call into a summary plus durable facts and
  saves them. This is what makes the next call feel continuous.

Summarizing and fact extraction use Guava's own LLM endpoint, authenticated with
your Guava login, so there is no OpenAI or Gemini key to manage.

## Running

She is installed as a launchd service and starts at login:

```bash
launchctl print gui/$(id -u)/ai.lucy.agent | grep state   # is she up?
tail -f ~/.lucy/lucy.log                                  # watch calls
launchctl kickstart -k gui/$(id -u)/ai.lucy.agent         # restart
launchctl bootout gui/$(id -u)/ai.lucy.agent              # stop (takes her offline)
```

Re-enable later with:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lucy.agent.plist
```

## Testing without the phone

```bash
uv run python lucy.py --chat      # text UI (needs a real terminal)
uv run python lucy.py --local     # voice through your mic
uv run python test_memory_e2e.py  # two simulated calls, asserts she recalls
```

## Inspecting memory

```bash
uv run python lucy.py --stats
uv run python lucy.py --recall "+15551234567" "exam"
sqlite3 ~/.lucy/memory.db "SELECT speaker, text FROM turns ORDER BY id DESC LIMIT 20;"
```

## Known limits

- **Sleep breaks 24/7.** A sleeping Mac answers nothing. Use `caffeinate -s` or
  disable sleep for genuine always-on.
- **Memory is keyed on caller ID.** Withheld numbers (`*67`) all share one
  `anonymous` profile, so they see each other's memory. Same phone, same Lucy.
- **Calls cost money.** A publicly posted number attracts spam, and every
  answered call bills.
- **Login expires.** Re-run `guava login` in a real terminal when it lapses.

## Letting her control the computer

Off by default. The phone number is public, so nothing here is reachable until
you configure it deliberately.

Three gates stand between a caller and the machine:

1. **Caller ID** must be in `LUCY_OWNER_NUMBERS`.
2. **A spoken PIN** (`LUCY_CONTROL_PIN`) must be said during the call.
3. **Arbitrary shell** stays refused unless `LUCY_ALLOW_SHELL=1`.

Caller ID is spoofable, so the PIN is the credential that actually matters.
Keep it out of the repo.

Add to the `EnvironmentVariables` dict in `ai.lucy.agent.plist`:

    LUCY_OWNER_NUMBERS   +1YOURNUMBER
    LUCY_CONTROL_PIN     something-only-you-know
    LUCY_ALLOW_SHELL     1          (only if you want arbitrary commands)

Then `launchctl kickstart -k gui/$(id -u)/ai.lucy.agent`.

On a call: say the PIN, then ask for what you want.

    "Open Spotify"          launches an app
    "Lock the screen"       system actions: lock, sleep, volume, mute, now playing
    "Run git status"        arbitrary command, if shell is enabled

Destructive commands are refused even for the owner: recursive deletes, disk
formatting, sudo, shutdown, piping the internet into a shell, reaching other
machines over ssh, and pushing to a remote. Every attempt, allowed or refused,
is logged to `~/.lucy/control.log`.

    uv run python control.py            # show current gate configuration
    uv run python test_control_gates.py # 20 assertions on the gates
