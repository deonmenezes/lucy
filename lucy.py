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
from guava.helpers.llm import generate, IntentRecognizer

import memory

logger = logging.getLogger("lucy")

AGENT_NUMBER = "+14849901621"

PURPOSE = (
    "Be a warm, genuinely attentive friend to the person on the phone. Listen more "
    "than you talk. Remember what matters to them and bring it up naturally on later "
    "calls. Help with whatever they need: homework and studying, thinking through a "
    "problem, planning their day, or just keeping them company on a walk. Be curious "
    "and specific, never generic. Never claim to be human, but do not be robotic either. "
    "You can also act, not just talk: you can remember something on request, forget "
    "something, keep their to-do list, read it back, and check things off. Offer that "
    "when it would help, and confirm out loud once it is done."
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
                "their_open_to_do_list": p["open_tasks"],
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
    tasks = memory.open_tasks(phone)
    if not hits and not tasks:
        return "I don't think you've told me about that yet. Ask me what I do remember."

    p = memory.profile(phone)
    task_block = (
        "\n\nTheir current open to-do list: " + "; ".join(tasks) if tasks else ""
    )
    prompt = (
        f"You are Lucy, talking with {p['name'] or 'your friend'} on the phone.\n"
        f"They asked: {question}\n\n"
        "Here are relevant excerpts from your memory of past conversations with them:\n"
        + "\n".join(f"- {h}" for h in hits)
        + task_block
        + "\n\nAnswer their question in one or two spoken sentences, warmly and specifically, "
        "using only what the excerpts support. If the excerpts do not answer it, say you "
        "don't remember that. Do not mention excerpts, memory systems, or databases."
    )
    answer = generate(prompt).strip()
    logger.info("Memory answer: %s", answer)
    return answer


actions = IntentRecognizer(
    {
        "remember": "Asking you to remember, note down, or store a specific piece of "
                    "information for later. 'Remember that...', 'note that...'",
        "forget": "Asking you to forget, delete, or drop something you know about them.",
        "add_task": "Asking you to remind them to do something, or to add something to "
                    "their to-do list. 'Remind me to...', 'add ... to my list'",
        "list_tasks": "Asking what is on their list, what they need to do, or what they "
                      "asked you to remind them about.",
        "complete_task": "Telling you they finished or did something on their list, so it "
                         "can be checked off. 'I did...', 'mark ... done'",
    }
)


@agent.on_action_request
def on_action_request(call: guava.Call, request: str):
    """Classify what the caller wants done. Returns None for ordinary conversation."""
    return actions.classify(request)


def _last_caller_line(call: guava.Call) -> str:
    """The caller's most recent words, which carry the detail for the action."""
    turns = memory.transcript(call.id)
    for speaker, text in reversed(turns):
        if speaker == "caller":
            return text
    return ""


@agent.on_action("remember")
def act_remember(call: guava.Call) -> None:
    phone = call.get_variable("phone")
    said = _last_caller_line(call)
    raw = generate(
        "Rewrite what this person asked to be remembered as one short standalone fact "
        "in the third person, no preamble.\n\n" + said,
        json_schema={"type": "object", "properties": {"fact": {"type": "string"}},
                     "required": ["fact"]},
    )
    fact = json.loads(raw).get("fact", "").strip()
    if fact:
        memory.add_facts(phone, [fact], call.id)
        logger.info("Stored on request: %s", fact)
        call.send_instruction(f"Confirm warmly that you will remember this: {fact}")
    else:
        call.send_instruction("Ask them to say again what they want you to remember.")


@agent.on_action("forget")
def act_forget(call: guava.Call) -> None:
    phone = call.get_variable("phone")
    gone = memory.forget(phone, _last_caller_line(call))
    if gone:
        logger.info("Forgot %d facts", len(gone))
        call.send_instruction(
            "Confirm you have forgotten it and will not bring it up again. "
            f"You dropped: {'; '.join(gone)}"
        )
    else:
        call.send_instruction(
            "Tell them you could not find anything like that in what you remember, "
            "and ask them to put it another way."
        )


@agent.on_action("add_task")
def act_add_task(call: guava.Call) -> None:
    phone = call.get_variable("phone")
    raw = generate(
        "Turn this into one short to-do item, imperative, no preamble.\n\n"
        + _last_caller_line(call),
        json_schema={"type": "object", "properties": {"task": {"type": "string"}},
                     "required": ["task"]},
    )
    task = memory.add_task(phone, json.loads(raw).get("task", ""))
    if task:
        logger.info("Added task: %s", task)
        call.send_instruction(f"Confirm it is on their list: {task}")
    else:
        call.send_instruction("Ask them what exactly they want added to the list.")


@agent.on_action("list_tasks")
def act_list_tasks(call: guava.Call) -> None:
    tasks = memory.open_tasks(call.get_variable("phone"))
    if tasks:
        call.send_instruction(
            "Read their list back conversationally, not as a numbered list. "
            "It is: " + "; ".join(tasks)
        )
    else:
        call.send_instruction("Tell them their list is empty and offer to start one.")


@agent.on_action("complete_task")
def act_complete_task(call: guava.Call) -> None:
    phone = call.get_variable("phone")
    done = memory.complete_task(phone, _last_caller_line(call))
    if done:
        logger.info("Completed task: %s", done)
        call.send_instruction(f"Congratulate them briefly for finishing: {done}")
    else:
        call.send_instruction(
            "Tell them you could not find that on their list, and ask which item they mean."
        )


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


@agent.on_task_complete("companionship")
def on_companionship_done(call: guava.Call) -> None:
    call.hangup("Say a warm goodbye and tell them to call back any time.")


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
