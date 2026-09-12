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
        f"Good morning. {total} new overnight."
        f'<break time="400ms"/> '
        f"{silent} sorted, you don't need to hear about them."
        f'<break time="400ms"/> '
        f"{spoken} need you."
    )


COUNTERS = ["First", "Second", "Third", "Fourth", "Fifth",
            "Sixth", "Seventh", "Eighth"]


def spoken_subject(subject):
    """Trim a subject line down to something a person would say aloud."""
    s = subject.replace("Re: ", "")
    # Drop anything after a dash or colon -- it is usually an email-ism.
    s = re.split(r"\s+[-\u2013\u2014]\s+|:\s+", s)[0]
    return s.rstrip("?.!").strip()


def item_line(item, index):
    """One item, one sentence. Prior state leads when it exists."""
    sender = item["from"].split("<")[0].strip()
    counter = COUNTERS[index - 1] if index <= len(COUNTERS) else f"Number {index}"
    subject = spoken_subject(item["subject"])

    if item.get("returned_to_you"):
        d = date.fromisoformat(item["prior_since"])
        return (
            f"{counter}: {sender}, on the {subject.lower()}."
            f'<break time="300ms"/> '
            f"You've had this in Waiting For since the {ordinal(d.day)}."
            f'<break time="300ms"/> '
            f"They've replied, so it's back on you."
        )
    return f"{counter}: {sender}. {subject}."


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
    parts.append("That's everything. The rest is filed.")
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
