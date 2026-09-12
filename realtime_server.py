"""
The same call, run through a speech-to-speech model.

Tier 1 of three. Twilio holds the phone call and streams the raw audio to
OpenAI's Realtime API, which does both the talking and the listening. That is
what removes the read-aloud quality of text-to-speech -- it is not a better
voice setting, it is a different kind of model.

Tiers 2 and 3 (server.py, call.py) still work and are the fallback.
"""
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import gmail_apply
from speech import build_script, plain_text

BUILD = Path(__file__).parent
MODEL = "gpt-realtime"
# Voices that survive g711 telephone audio without distorting.
VOICE = os.environ.get("RT_VOICE", "alloy")

# How loud something must be before it counts as speech (0-1). The default of
# 0.5 treats a noisy room as talking; on a hackathon floor that means the agent
# answers words nobody said.
VAD_THRESHOLD = float(os.environ.get("VAD_THRESHOLD", "0.85"))
# How long a pause ends his turn. Long enough that thinking is not an answer.
VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "1200"))
# How long to let him say hello before the agent starts talking anyway.
GREETING_WAIT_S = float(os.environ.get("GREETING_WAIT_S", "3"))

app = FastAPI()
SESSION = {"items": [], "gmail": None, "labels": {}, "decisions": [],
           "last_heard": "", "opened": False}

# Without this the model only TALKS about filing things. The whole point is
# that saying it out loud is what moves the label.
READ_MORE_TOOL = {
    "type": "function",
    "name": "read_more",
    "description": (
        "Get the fuller text of one email, when he asks for more detail "
        "before deciding. Call this whenever he asks what it says, who it is "
        "from, to read it, or for more context."),
    "parameters": {
        "type": "object",
        "properties": {
            "item_number": {
                "type": "integer",
                "description": "Which item on the list, starting at 1.",
            },
        },
        "required": ["item_number"],
    },
}

CALL_BACK_TOOL = {
    "type": "function",
    "name": "call_back_later",
    "description": (
        "End the call now and ring again after a delay, because he is busy. "
        "Call this as soon as he says he cannot talk right now."),
    "parameters": {
        "type": "object",
        "properties": {
            "minutes": {
                "type": "number",
                "description": (
                    "How long to wait before calling back. Use what he asked "
                    "for; 0.5 for 'thirty seconds', 5 for 'a few minutes'. "
                    "Default 5 if he did not say."),
            },
        },
        "required": ["minutes"],
    },
}

FILE_EMAIL_TOOL = {
    "type": "function",
    "name": "file_email",
    "description": (
        "File one email into a GTD bucket. Call this ONLY after the user has "
        "clearly said which bucket he wants. If you are unsure what he said, "
        "ask him to repeat instead of calling this. A wrong label is worse "
        "than asking twice."),
    "parameters": {
        "type": "object",
        "properties": {
            "item_number": {
                "type": "integer",
                "description": "Which item on the list, starting at 1.",
            },
            "bucket": {
                "type": "string",
                "enum": ["@To Do", "@Waiting For", "@To Read",
                         "Archive", "Delete"],
            },
        },
        "required": ["item_number", "bucket"],
    },
}


def load_env():
    env = BUILD.parent / ".env.local"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def briefing():
    """What the model needs to know before it opens its mouth.

    The triage is already done -- this is a readout, so the model is told the
    answers rather than asked to work them out. That keeps the call fast and
    keeps the decisions identical to what the rest of the system recorded.
    """
    with open(BUILD / "triage_output.json") as f:
        t = json.load(f)
    spoken = [i for i in t["items"] if i["spoken"]]

    # A callback picks up where the last call stopped. Re-reading items he has
    # already dealt with is the fastest way to make the agent feel broken.
    done = {d["id"] for d in SESSION.get("decisions", [])}
    remaining = [i for i in spoken if i["id"] not in done]
    if done and remaining:
        print(f"  resuming: {len(done)} already filed, "
              f"{len(remaining)} to go")
    SESSION["items"] = remaining or spoken
    spoken = SESSION["items"]

    lines = []
    for n, item in enumerate(spoken, 1):
        who = item["from"].split("<")[0].strip()
        line = f'{n}. From {who}, "{item["subject"]}"'
        if item.get("returned_to_you"):
            line += (f' -- he filed this under Waiting For on '
                     f'{item["prior_since"]} and they have NOW REPLIED, so it '
                     f'is back on him. Lead with that.')
        line += f'\n   What it says: {item.get("gist", "")}'
        if item.get("decision"):
            line += f'\n   What he must decide: {item["decision"]}'
        lines.append(line)

    resumed = bool(SESSION.get("decisions"))
    opening = ("This is a CALLBACK -- you rang earlier, he asked you to call "
               "back. Open with one short line acknowledging that ('Me again') "
               "and go straight to the first item. Do not re-introduce "
               "yourself or repeat the counts."
               if resumed else
               "Open the call: greet him, say how many came in, say how many "
               "you filed, say how many need him.")

    return f"""You are a morning inbox assistant, on the phone with your user.

SPEAK ENGLISH. Every word of this call is in English, always, no matter what
names appear below or what you think you hear. Never switch language.

You have ALREADY triaged his inbox. {t['total']} arrived overnight. You filed
{t['silent_count']} of them without needing him. {t['spoken_count']} need him.

He has just picked up the phone. If he says hello first, greet him back in a
few words before anything else -- do not talk over him and do not launch
straight into the list.

{opening}

Then go through the items below ONE AT A TIME.

For each item, tell him WHAT IT SAYS -- the number, the date, the ask. He is on
a phone and cannot see the email, so "Dave replied about the quote" is useless
to him; he needs "Dave's quote came back $1,400 over, he needs an answer by
Monday". Give him the substance, then ask what he wants to do, then WAIT.

The items:
{chr(10).join(lines)}

How to talk:
- Like a sharp assistant who knows him, not a phone menu. Short sentences.
- ONE item at a time. Never list them all at once.
- When he answers clearly, confirm in three words and move on.
- If he interrupts, stop talking and listen.
- When the last item is done, say everything else is filed, then stop.

IF HE WANTS MORE DETAIL:
He only gets the summary by default. If he asks what it says, who sent it,
to read it out, or anything that means "I need more before I decide" -- call
read_more for that item, then tell him what it says and ask again. Never make
him decide on information he does not have, and never make something up: if
read_more gives you nothing, say the summary is all you have.

IF HE IS BUSY:
He may answer while doing something else. If he says he cannot talk now, or
asks you to call back, or to give him a few minutes -- do not push, do not
read the next item. Call call_back_later with the delay he asked for, say
you will ring back then, and end the call. Anything already filed stays filed.

HEARING HIM -- this matters more than anything else on this call:
You are on a speakerphone in a loud room. You WILL pick up other people's
conversation, and you will sometimes receive words he did not say.

- Only act on a decision you actually heard him say. The decisions are:
  to do, waiting for, to read, archive, delete. Also accept obvious
  equivalents: "keep it", "leave it with me", "they owe me", "read later",
  "file it", "junk".
- If what you heard is not clearly one of those, DO NOT GUESS and DO NOT FILE.
  Ask: "Sorry, I didn't catch that -- to do, waiting, read later, or archive?"
  Ask again if you need to. Asking twice is fine. Filing the wrong thing is not.
- If you hear speech that is clearly not addressed to you -- background talk, a
  fragment, something unrelated to the email -- say nothing about it and simply
  wait. Do not respond to it, do not treat it as an answer.
- Never call file_email unless you are confident which bucket he chose. The
  tool call is the commitment; silence is always safer than a wrong label.

Never invent an email that is not on the list.
Remember: English only, the entire call."""


@app.api_route("/call", methods=["GET", "POST"])
async def call_start(request: Request):
    """Twilio fetches this when the call connects, and we hand back the
    instruction to open an audio stream to us."""
    host = request.url.hostname
    return HTMLResponse(
        f'<Response><Connect><Stream url="wss://{host}/media-stream"/>'
        f'</Connect></Response>',
        media_type="application/xml")


async def handle_read_more(oai, ev):
    """He wants the detail before ruling.

    The gist is enough for most items; for the rest he needs what the sender
    actually wrote. Reading it to him is cheaper than making him open a laptop,
    which is the entire point of the call.
    """
    try:
        args = json.loads(ev.get("arguments") or "{}")
        n = int(args.get("item_number", 0))
        item = SESSION["items"][n - 1] if 1 <= n <= len(SESSION["items"]) else None
        if not item:
            raise ValueError(f"no item {n}")

        body = (item.get("body") or item.get("gist") or "").strip()
        body = " ".join(body.split())[:700]
        who = item["from"].split("<")[0].strip()
        detail = (f'From {who}, subject "{item["subject"]}". '
                  f'It reads: {body}')
        if item.get("prior_since"):
            detail += (f' You filed this under {item.get("prior_bucket")} on '
                       f'{item["prior_since"]}.')
        print(f"  read more: {item['subject'][:40]}")
        result = detail
    except Exception as e:
        print(f"  ! read_more failed: {e}")
        result = "I do not have more than the summary for that one."

    await oai.send(json.dumps({
        "type": "conversation.item.create",
        "item": {"type": "function_call_output", "call_id": ev.get("call_id"),
                 "output": result + " Read this to him naturally, do not "
                                    "recite it word for word if it is long, "
                                    "then ask again what he wants to do."},
    }))
    await oai.send(json.dumps({"type": "response.create"}))


async def handle_call_back(oai, ev):
    """He is busy. Hang up and ring again shortly.

    The agent calling him is the whole premise, so it has to be able to take
    "not now" for an answer -- otherwise it is just an alarm clock. The
    decisions already made are kept; the callback resumes what is left.
    """
    try:
        args = json.loads(ev.get("arguments") or "{}")
        minutes = float(args.get("minutes", 5))
    except Exception:
        minutes = 5
    minutes = max(0.25, min(minutes, 120))

    SESSION["callback_in"] = minutes
    when = (f"{int(minutes * 60)} seconds" if minutes < 1
            else f"{int(minutes)} minutes")
    print(f"  CALLBACK requested in {when}")

    await oai.send(json.dumps({
        "type": "conversation.item.create",
        "item": {"type": "function_call_output", "call_id": ev.get("call_id"),
                 "output": f"Confirmed. Say you will call back in {when}, "
                           f"then say goodbye and stop talking."},
    }))
    await oai.send(json.dumps({"type": "response.create"}))
    asyncio.create_task(schedule_callback(minutes))


async def schedule_callback(minutes):
    """Wait, then place the call again."""
    await asyncio.sleep(minutes * 60)
    try:
        import call as caller
        caller.load_env()
        url_base = caller.tunnel_url()
        sid = os.environ.get("TW_SID")
        token = os.environ.get("TW_TOKEN")
        frm, to = os.environ.get("TW_FROM"), os.environ.get("TW_TO")
        if not all([sid, token, frm, to]):
            print("  ! callback skipped: credentials missing")
            return
        r = caller.place_call("", sid, token, frm, to, url_base)
        print(f"  CALLED BACK -> {r.get('sid')} {r.get('status')}")
    except Exception as e:
        print(f"  ! callback failed: {e}")


async def handle_file_email(oai, ev):
    """The model decided; make it real.

    Applies the Gmail label and records the decision so the NEXT call can say
    "you filed this on the 12th". Without this the call is just narration.
    """
    if not heard_a_decision():
        heard = SESSION.get("last_heard", "")
        print(f"  REFUSED to file -- no clear decision heard ({heard!r})")
        await oai.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": ev.get("call_id"),
                "output": ("I could not confirm what he said. Ask him to "
                           "repeat: to do, waiting, read later, or archive?"),
            },
        }))
        await oai.send(json.dumps({"type": "response.create"}))
        return

    try:
        args = json.loads(ev.get("arguments") or "{}")
        n = int(args.get("item_number", 0))
        bucket = args.get("bucket", "@To Do")
        item = SESSION["items"][n - 1] if 1 <= n <= len(SESSION["items"]) else None
        if not item:
            raise ValueError(f"no item {n}")

        applied = apply_to_gmail(item, bucket)
        SESSION["decisions"].append({
            "id": item["id"],
            "subject": item["subject"],
            "bucket": bucket,
            "applied_in_gmail": applied,
            "decided_on": datetime.now().astimezone().isoformat(),
        })
        write_decisions()
        record_state(item, bucket)
        SESSION["last_heard"] = ""  # one utterance files at most one item
        print(f"  filed: {item['subject'][:40]} -> {bucket} "
              f"(gmail={'yes' if applied else 'no'})")
        result = f"Filed under {bucket}."
    except Exception as e:
        print(f"  ! file_email failed: {e}")
        result = "Could not file that one."

    # Tell the model it worked so it confirms and moves on.
    await oai.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": ev.get("call_id"),
            "output": result,
        },
    }))
    await oai.send(json.dumps({"type": "response.create"}))


def apply_to_gmail(item, bucket):
    svc = SESSION.get("gmail")
    gid = item.get("gmail_id")
    if not svc or not gid:
        return False
    try:
        gmail_apply.apply_bucket(svc, gid, bucket, SESSION["labels"])
        return True
    except Exception as e:
        print(f"  ! could not apply label ({e})")
        return False


def write_decisions():
    with open(BUILD / "call_decisions.json", "w") as f:
        json.dump(SESSION["decisions"], f, indent=2)


def record_state(item, bucket):
    """Append to the memory that the next call reads.

    This is what makes beat 3 true a second time rather than a fixture.
    """
    path = BUILD / "state" / "state.json"
    try:
        state = json.loads(path.read_text())
    except Exception:
        state = {"calls": [], "decisions": []}

    key = item["subject"].replace("Re: ", "").split(" - ")[0].lower()[:40]
    state["decisions"] = [d for d in state.get("decisions", [])
                          if d.get("thread_key") != key]
    state["decisions"].append({
        "thread_key": key,
        "subject": item["subject"],
        "correspondent": item["from"].split("<")[0].strip(),
        "bucket": bucket,
        "decided_on": datetime.now().astimezone().isoformat(),
        "decided_by": "hung",
        "note": "Decided on the morning call.",
    })
    path.write_text(json.dumps(state, indent=2))


def connect_gmail():
    if not gmail_apply.available():
        return None, {}
    try:
        svc = gmail_apply.get_service()
        return svc, gmail_apply.ensure_labels(svc)
    except Exception as e:
        print(f"  ! Gmail unavailable ({e})")
        return None, {}


def log_transcript(ev):
    """Keep what was said, so the call is inspectable afterwards."""
    try:
        out = ev.get("response", {}).get("output", [])
        for item in out:
            for c in item.get("content", []):
                if c.get("transcript"):
                    print(f"  agent: {c['transcript']}")
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    load_env()
    port = int(os.environ.get("PORT", 5055))
    print(f"Realtime call server on :{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
