"""Lucy - an AI friend you can call, with memory that persists between calls.

Lucy answers the phone, talks about whatever you want, and writes everything to
a local SQLite database. On your next call she loads what she knows about you
and picks up where you left off. She can search her whole history to answer
questions about your life.

Run 24/7 (answers the real phone number):
    uv run python lucy.py --serve

Test without a phone:
    uv run python lucy.py --chat
    uv run python lucy.py --local

Inspect what she remembers:
    uv run python lucy.py --stats
    uv run python lucy.py --recall "+1555..." "my exam"
"""

from __future__ import annotations

import argparse
import json
import logging

import guava
from guava import Agent, AcceptCall, Runner, logging_utils
from guava.helpers.llm import generate

import memory

logger = logging.getLogger("lucy")

AGENT_NUMBER = "+14849901621"

PURPOSE = (
    "Be a warm, genuinely attentive friend to the person on the phone. Listen more "
    "than you talk. Remember what matters to them and bring it up naturally on later "
    "calls. Help with whatever they need: homework and studying, thinking through a "
    "problem, planning their day, or just keeping them company on a walk. Be curious "
    "and specific, never generic. Never claim to be human, but do not be robotic either."
)

agent = Agent(
    name="Lucy",
    organization="Lucy",
    purpose=PURPOSE,
)


def caller_key(call: guava.Call) -> str:
    """Stable per-person memory key. Phone number when we have one."""
    info = call.call_info
    if getattr(info, "call_type", None) == "pstn":
        return info.from_number or "anonymous"
    if getattr(info, "call_type", None) == "sip":
        return f"sip:{info.from_aor}"
    return "local-test"


@agent.on_call_received
def on_call_received(call_info) -> guava.IncomingCallAction:
    logger.info("Incoming call: %s", call_info)
    return AcceptCall()


@agent.on_call_start
def on_call_start(call: guava.Call) -> None:
    phone = caller_key(call)
    call.set_variable("phone", phone)
    memory.ensure_caller(phone)
    memory.start_call(call.id, phone)

    p = memory.profile(phone)

    if p["known"]:
        # Hand Lucy everything she knows before she opens her mouth.
        call.add_info(
            "what you remember about this person",
            {
                "their_name": p["name"],
                "calls_so_far": p["call_count"],
                "last_spoke": p["last_seen"],
                "things_you_know": p["facts"],
                "recent_conversations": p["recent_calls"],
            },
        )
        greeting = (
            f"Greet them warmly by name ({p['name']}) if you know it. Reference something "
            "specific you remember from a previous call and ask how it went. Do not recite "
            "a list of facts, just mention one thing naturally."
            if p["name"]
            else "Greet them warmly as someone you have spoken to before, and reference "
            "something specific you remember."
        )
    else:
        greeting = (
            "This is the first time you have spoken. Introduce yourself as Lucy, warmly and "
            "briefly. Ask their name early and use it. Let them know you will remember your "
            "conversations so they can pick up where they left off next time."
        )

    call.set_task(
        "companionship",
        objective=(
            "Be their friend for as long as they want to talk. "
            + greeting
            + " Follow their lead on topics. Ask follow-up questions about the details of "
            "their life. If they ask for help with homework or a problem, work through it "
            "with them step by step rather than just giving an answer."
        ),
        completion_criteria="Only when the caller clearly wants to end the conversation.",
    )


@agent.on_caller_speech
def on_caller_speech(call: guava.Call, event) -> None:
    memory.record_turn(call.id, call.get_variable("phone"), "caller", event.utterance)


@agent.on_agent_speech
def on_agent_speech(call: guava.Call, event) -> None:
    memory.record_turn(call.id, call.get_variable("phone"), "agent", event.utterance)


@agent.on_question
def on_question(call: guava.Call, question: str) -> str:
    """Search everything Lucy has ever stored for this caller, then answer."""
    phone = call.get_variable("phone")
    hits = memory.search(phone, question, k=10)
    if not hits:
        return "I don't think you've told me about that yet. Ask me what I do remember."

    p = memory.profile(phone)
    prompt = (
        f"You are Lucy, talking with {p['name'] or 'your friend'} on the phone.\n"
        f"They asked: {question}\n\n"
        "Here are relevant excerpts from your memory of past conversations with them:\n"
        + "\n".join(f"- {h}" for h in hits)
        + "\n\nAnswer their question in one or two spoken sentences, warmly and specifically, "
        "using only what the excerpts support. If the excerpts do not answer it, say you "
        "don't remember that. Do not mention excerpts, memory systems, or databases."
    )
    answer = generate(prompt).strip()
    logger.info("Memory answer: %s", answer)
    return answer


FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The caller's first name if they said it, else empty string.",
        },
        "summary": {
            "type": "string",
            "description": "Two or three sentences on what this conversation was about.",
        },
        "facts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Durable facts worth remembering about this person: people, "
            "plans, preferences, ongoing situations. One short sentence each. "
            "Skip small talk and anything only true during this call.",
        },
    },
    "required": ["name", "summary", "facts"],
}


@agent.on_session_end
def on_session_end(call: guava.Call, ended) -> None:
    """Distill the call into durable memory. This is what makes Lucy remember."""
    phone = call.get_variable("phone")
    reason = getattr(ended, "termination_reason", None)
    turns = memory.transcript(call.id)

    if not turns:
        memory.end_call(call.id, None, str(reason))
        return

    convo = "\n".join(f"{'Them' if s == 'caller' else 'Lucy'}: {t}" for s, t in turns)
    known = memory.profile(phone)

    try:
        raw = generate(
            "Read this phone conversation between Lucy (an AI friend) and her friend, "
            "then extract what Lucy should remember.\n\n"
            f"Facts Lucy already knew (do not repeat these): {known['facts']}\n\n"
            f"Conversation:\n{convo}",
            json_schema=FACT_SCHEMA,
        )
        data = json.loads(raw)
    except Exception:
        logger.exception("Could not distill memory; saving raw transcript only.")
        memory.end_call(call.id, None, str(reason))
        return

    if data.get("name") and not known["name"]:
        memory.set_name(phone, data["name"])
    added = memory.add_facts(phone, data.get("facts", []), call.id)
    memory.end_call(call.id, data.get("summary"), str(reason))
    logger.info("Remembered %d new facts about %s", added, phone)


if __name__ == "__main__":
    logging_utils.configure_logging()

    parser = argparse.ArgumentParser(description="Lucy, an AI friend with memory.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true", help="Answer the real phone 24/7.")
    mode.add_argument("--chat", action="store_true", help="Text chat (testing).")
    mode.add_argument("--local", action="store_true", help="Voice via your mic (testing).")
    mode.add_argument("--stats", action="store_true", help="Show what Lucy remembers.")
    mode.add_argument("--recall", nargs=2, metavar=("PHONE", "QUERY"), help="Query memory.")
    parser.add_argument("--number", default=AGENT_NUMBER, help="Number to answer on.")
    args = parser.parse_args()

    if args.stats:
        print(json.dumps(memory.stats(), indent=2))
    elif args.recall:
        phone, query = args.recall
        print(json.dumps(memory.profile(phone), indent=2))
        for hit in memory.search(phone, query):
            print(" -", hit)
    elif args.serve:
        logger.info("Lucy is listening on %s. Press Ctrl+C to drain and stop.", args.number)
        Runner().listen_phone(agent, args.number).run()
    elif args.chat:
        agent.chat()
    else:
        agent.call_local()
