"""
The two-way call.

Tier 2 of three: Twilio holds the call and posts what Hung says back to this
server, which decides what happens next. Needs a public URL (ngrok) so Twilio
can reach the laptop.

Uses <Gather input="speech">, which listens -- unlike <Say> on its own. Cheaper
and far more predictable on venue wifi than streaming audio to a Realtime model,
and on a phone call the difference is barely audible.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from flask import Flask, Response, request

import gmail_apply
from speech import build_script, item_line, VOICE_HINTS

BUILD = Path(__file__).parent
VOICE = os.environ.get("VOICE", "Polly.Danielle-Generative")

app = Flask(__name__)

# Loaded once at call start so the phone loop never waits on a file read.
SESSION = {"items": [], "index": 0, "decisions": [], "gmail": None,
           "labels": {}}


def connect_gmail():
    """Attach Gmail if it is set up. Silent no-op when it is not, so the
    voice demo is never blocked by missing credentials."""
    if not gmail_apply.available():
        return None, {}
    try:
        svc = gmail_apply.get_service()
        return svc, gmail_apply.ensure_labels(svc)
    except Exception as e:
        print(f"  ! Gmail unavailable ({e}); labels will not be applied.")
        return None, {}


def say(text, gather=False, hints=None):
    """Build TwiML. When gather is set, the call listens after speaking."""
    body = f'<Say voice="{VOICE}">{text}</Say>'
    if not gather:
        return f"<Response>{body}</Response>"
    hint_attr = f' hints="{escape(hints)}"' if hints else ""
    return (
        "<Response>"
        f'<Gather input="speech" action="/decide" method="POST" '
        f'speechTimeout="auto" timeout="6"{hint_attr}>{body}</Gather>'
        '<Say voice="' + VOICE + '">I did not catch that. Moving on.</Say>'
        '<Redirect method="POST">/next</Redirect>'
        "</Response>"
    )


def load_session():
    with open(BUILD / "triage_output.json") as f:
        t = json.load(f)
    SESSION["items"] = [i for i in t["items"] if i["spoken"]]
    SESSION["index"] = 0
    SESSION["decisions"] = []
    SESSION["gmail"], SESSION["labels"] = connect_gmail()
    return t


@app.route("/call", methods=["POST", "GET"])
def call_start():
    """First thing Twilio hits when the call connects."""
    t = load_session()
    opening = (
        f"Good morning. {t['total']} new overnight."
        f'<break time="400ms"/> '
        f"{t['silent_count']} sorted, you don't need to hear about them."
        f'<break time="400ms"/> '
        f"{t['spoken_count']} need you."
        f'<break time="700ms"/>'
    )
    first = item_line(SESSION["items"][0], 1) if SESSION["items"] else ""
    prompt = f'{opening}{first}<break time="400ms"/>What do you want to do?'
    return Response(say(prompt, gather=True, hints=VOICE_HINTS),
                    mimetype="text/xml")


@app.route("/decide", methods=["POST"])
def decide():
    """Hung said something. Record it and move to the next item."""
    heard = (request.form.get("SpeechResult") or "").lower().strip()
    idx = SESSION["index"]

    if idx < len(SESSION["items"]):
        item = SESSION["items"][idx]
        bucket = interpret(heard)
        applied = apply_to_gmail(item, bucket)
        SESSION["decisions"].append({
            "id": item["id"],
            "subject": item["subject"],
            "heard": heard,
            "bucket": bucket,
            "applied_in_gmail": applied,
            "decided_on": datetime.now().astimezone().isoformat(),
        })
        write_decisions()
        ack = confirm_phrase(bucket, heard)
    else:
        ack = ""

    SESSION["index"] += 1
    return Response(next_twiml(ack), mimetype="text/xml")


@app.route("/next", methods=["POST", "GET"])
def next_item():
    SESSION["index"] += 1
    return Response(next_twiml(""), mimetype="text/xml")


def next_twiml(ack):
    idx = SESSION["index"]
    if idx >= len(SESSION["items"]):
        closing = f'{ack}<break time="500ms"/>That is everything. The rest is filed.'
        return say(closing)
    nxt = item_line(SESSION["items"][idx], idx + 1)
    prompt = f'{ack}<break time="500ms"/>{nxt}<break time="400ms"/>What do you want to do?'
    return say(prompt, gather=True, hints=VOICE_HINTS)


def apply_to_gmail(item, bucket):
    """Move the label for real, while the caller is still on the phone.

    This is the moment the demo turns on: he says a word, and the label moves
    on screen. Failure here must never break the call, so it is caught.
    """
    svc = SESSION.get("gmail")
    gid = item.get("gmail_id")
    if not svc or not gid:
        return False
    try:
        gmail_apply.apply_bucket(svc, gid, bucket, SESSION["labels"])
        return True
    except Exception as e:
        print(f"  ! Could not apply label ({e})")
        return False


def interpret(heard):
    """Map what he actually said onto a bucket.

    Deliberately generous -- phone speech recognition is lossy, and on camera a
    misheard word reads as a broken agent. Order matters: check the more
    specific phrases first.
    """
    if not heard:
        return "@To Do"
    if any(w in heard for w in ["waiting", "wait for", "waiting for", "they owe"]):
        return "@Waiting For"
    if any(w in heard for w in ["read", "reading", "later", "to read"]):
        return "@To Read"
    if any(w in heard for w in ["archive", "file it", "done", "nothing"]):
        return "Archive"
    if any(w in heard for w in ["delete", "junk", "trash", "bin"]):
        return "Delete"
    if any(w in heard for w in ["draft", "reply", "respond", "todo",
                                "to do", "keep"]):
        return "@To Do"
    return "@To Do"


def confirm_phrase(bucket, heard):
    """Say back what was done. Short -- this is mid-call, not a summary."""
    spoken = {
        "@To Do": "Keeping that on your list.",
        "@Waiting For": "Moved to Waiting For.",
        "@To Read": "Filed under To Read.",
        "Archive": "Archived.",
        "Delete": "Deleted.",
    }
    return spoken.get(bucket, "Done.")


def write_decisions():
    """Persist immediately, not at hang-up.

    If the call drops mid-way the decisions already made must survive -- that is
    the whole premise of the next call knowing what happened on this one.
    """
    out = BUILD / "call_decisions.json"
    with open(out, "w") as f:
        json.dump(SESSION["decisions"], f, indent=2)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Call server on :{port}. Expose with: ngrok http {port}")
    app.run(host="0.0.0.0", port=port)
