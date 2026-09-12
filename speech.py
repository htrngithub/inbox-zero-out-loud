"""
Turn triage output into what the agent actually says on the phone.

Separated from the call transport on purpose: the same script is spoken
whether it goes out over Twilio, over the laptop speakers, or into a test.
"""
import json
import re
from datetime import date
from pathlib import Path

BUILD = Path(__file__).parent

ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 21: "21st", 22: "22nd", 23: "23rd",
            31: "31st"}


def ordinal(day):
    return ORDINALS.get(day, f"{day}th")


def opening_line(t):
    """The line that makes the silence audible."""
    total, spoken, silent = t["total"], t["spoken_count"], t["silent_count"]
    return (
        f"Morning."
        f'<break time="450ms"/> '
        f"You got {total} overnight."
        f'<break time="350ms"/> '
        f"I've filed {silent} of them,"
        f'<break time="200ms"/> '
        f"you don't need to hear about those."
        f'<break time="450ms"/> '
        f"{spoken} actually need you."
    )


# How a person actually moves through a list out loud. Not "First:", "Second:"
# -- those read as a form being filled in.
OPENERS = ["Okay, first up", "Next", "Then", "After that", "Last one",
           "And then", "One more"]

# Varied so two back-to-back returns do not read as a template.
RETURNED = [
    "{who} finally got back to you on the {subject}.",
    "{who} replied on the {subject}.",
    "{who} came back to you about the {subject}.",
]


def spoken_subject(subject):
    """Trim a subject line down to something a person would say aloud."""
    s = subject.replace("Re: ", "")
    # Drop anything after a dash or colon -- it is usually an email-ism.
    s = re.split(r"\s+[-\u2013\u2014]\s+|:\s+", s)[0]
    return s.rstrip("?.!").strip()


def first_name(sender):
    """'Marcus Webb <m@x.com>' -> 'Marcus'. Falls back to the org name."""
    name = sender.split("<")[0].strip().strip('"')
    if not name:
        return "someone"
    parts = name.split()
    # A person gets a first name; a company keeps its whole name.
    if len(parts) == 2 and all(p[:1].isupper() for p in parts):
        return parts[0]
    return name


def item_line(item, index):
    """One item, said the way a person would say it.

    Prior state leads when it exists, because "they finally got back to you"
    is the most useful thing the agent knows.
    """
    who = first_name(item["from"])
    subject = spoken_subject(item["subject"]).lower()
    opener = OPENERS[min(index - 1, len(OPENERS) - 1)]

    if item.get("returned_to_you"):
        d = date.fromisoformat(item["prior_since"])
        lead = RETURNED[(index - 1) % len(RETURNED)].format(
            who=who, subject=subject)
        tail = ("so that's back on your plate now."
                if index == 1 else "so that one's yours again.")
        return (
            f"{opener} -- {lead}"
            f'<break time="350ms"/> '
            f"You'd been waiting on that since the {ordinal(d.day)},"
            f'<break time="200ms"/> '
            f"{tail}"
        )

    # The model's reason usually names the person already; saying the name
    # first as well gives you "Priya. Priya needs your availability."
    reason = item.get("reason", "").strip().rstrip(".")
    if reason and len(reason) < 60:
        if who.lower() in reason.lower():
            return f'{opener} -- {reason}.'
        return f'{opener} -- {who}.<break time="250ms"/> {reason.capitalize()}.'
    return f'{opener} -- {who}, about the {subject}.'


def build_script(triage_path=None):
    triage_path = triage_path or BUILD / "triage_output.json"
    with open(triage_path) as f:
        t = json.load(f)

    spoken = [i for i in t["items"] if i["spoken"]]
    parts = [opening_line(t)]
    for n, item in enumerate(spoken, 1):
        parts.append('<break time="600ms"/>')
        parts.append(item_line(item, n))
    parts.append('<break time="600ms"/>')
    parts.append("That's it.<break time=\"300ms\"/> "
                 "Everything else is already filed.")
    return "".join(parts), spoken


def plain_text(ssml):
    """The same script with the SSML stripped, for reading on a laptop."""
    import re
    txt = re.sub(r'<break time="\d+ms"/>', " ", ssml)
    return re.sub(r"\s+", " ", txt).strip()


if __name__ == "__main__":
    ssml, spoken = build_script()
    print("=== What the agent says ===\n")
    print(plain_text(ssml))
    print(f"\n=== {len(spoken)} items spoken ===")


# Words to bias phone speech recognition toward. Twilio weights these, which
# matters because "waiting for" is the phrase most likely to be misheard and
# the one that carries the demo.
VOICE_HINTS = ("todo, to do, waiting for, waiting, to read, read later, "
               "archive, delete, draft it, reply, keep")
