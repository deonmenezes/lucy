"""Starter Guava voice agent.

Run one of:
    uv run python agent.py --chat            # text-only session, no phone needed
    uv run python agent.py --local           # local voice call through your mic
    uv run python agent.py --phone +15551234567 --name "John Doe"

Requires auth: either `guava login` (CLI) or GUAVA_API_KEY in the environment.
"""

import argparse

import guava
from guava import Agent, logging_utils

agent = Agent(
    organization="Virelity",
    purpose="Call the person back and find out what they need help with.",
)


@agent.on_call_start
def on_call_start(call: guava.Call) -> None:
    call.reach_person(contact_full_name=call.get_variable("contact_name"))


@agent.on_reach_person
def on_reach_person(call: guava.Call, outcome: str) -> None:
    if outcome != "available":
        call.hangup("Appropriately end the call.")
        return

    call.set_task(
        "intake",
        checklist=[
            "Introduce yourself and say you're following up on their request.",
            guava.Field(
                key="reason",
                field_type="string",
                description="What the caller needs help with, in their own words.",
            ),
            "Confirm you've noted it and let them ask any questions.",
        ],
    )


@agent.on_task_complete("intake")
def on_intake_complete(call: guava.Call) -> None:
    call.hangup("Thank them for their time and end the call.")


if __name__ == "__main__":
    logging_utils.configure_logging()

    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--phone", metavar="PHONE_NUMBER", help="Call a real phone number.")
    mode.add_argument("--local", action="store_true", help="Local voice call (testing).")
    mode.add_argument("--chat", action="store_true", help="Local text chat (testing).")
    parser.add_argument("--name", default="John Doe", help="Name of the contact.")
    parser.add_argument("--from-number", help="Your Guava number (required with --phone).")
    args = parser.parse_args()

    variables = {"contact_name": args.name}

    if args.phone:
        if not args.from_number:
            parser.error("--phone requires --from-number")
        agent.call_phone(
            from_number=args.from_number,
            to_number=args.phone,
            variables=variables,
        )
    elif args.chat:
        agent.chat(variables)
    else:
        agent.call_local(variables)
