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

app = FastAPI()
SESSION = {"items": [], "gmail": None, "labels": {}, "decisions": []}

# Without this the model only TALKS about filing things. The whole point is
# that saying it out loud is what moves the label.
FILE_EMAIL_TOOL = {
    "type": "function",
    "name": "file_email",
    "description": (
        "File one email into a GTD bucket. Call this immediately after the "
        "user says what to do with an item, before you speak again."),
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
    SESSION["items"] = spoken

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

    return f"""You are a morning inbox assistant, on the phone with your user.

SPEAK ENGLISH. Every word of this call is in English, always, no matter what
names appear below or what you think you hear. Never switch language.

You have ALREADY triaged his inbox. {t['total']} arrived overnight. You filed
{t['silent_count']} of them without needing him. {t['spoken_count']} need him.

Open the call like this, in your own words: greet him, say how many came in,
say how many you filed, say how many need him. Then go through the items below
ONE AT A TIME.

For each item, tell him WHAT IT SAYS -- the number, the date, the ask. He is on
a phone and cannot see the email, so "Dave replied about the quote" is useless
to him; he needs "Dave's quote came back $1,400 over, he needs an answer by
Monday". Give him the substance, then ask what he wants to do, then WAIT.

The items:
{chr(10).join(lines)}

How to talk:
- Like a sharp assistant who knows him, not a phone menu. Short sentences.
- ONE item at a time. Never list them all at once.
- When he answers, confirm in three words and move on. Do not repeat the item.
- His answers map to: To Do, Waiting For, To Read, Archive, Delete. If he says
  something else ("draft it", "leave it"), pick the closest and say which.
- If he interrupts, stop talking and listen.
- When the last item is done, say everything else is filed, then stop.
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


@app.api_route("/keypad", methods=["GET", "POST"])
async def keypad(request: Request):
    """Decisions by keypad instead of voice.

    A room loud enough to fool speech detection cannot fake a keypress. Same
    triage, same labels, same state -- only the input changes. Kept as the
    path that cannot be defeated by a noisy venue.
    """
    form = await request.form() if request.method == "POST" else {}
    digit = (form.get("Digits") or "").strip()
    idx = int(request.query_params.get("i", "0"))

    if not SESSION["items"]:
        load_items()

    if digit and 0 <= idx - 1 < len(SESSION["items"]):
        bucket = KEYPAD_BUCKETS.get(digit, "@To Do")
        item = SESSION["items"][idx - 1]
        applied = apply_to_gmail(item, bucket)
        SESSION["decisions"].append({
            "id": item["id"], "subject": item["subject"], "bucket": bucket,
            "applied_in_gmail": applied,
            "decided_on": datetime.now().astimezone().isoformat(),
        })
        write_decisions()
        record_state(item, bucket)
        print(f"  filed (keypad): {item['subject'][:38]} -> {bucket} "
              f"(gmail={'yes' if applied else 'no'})")

    return HTMLResponse(keypad_twiml(idx), media_type="application/xml")


KEYPAD_BUCKETS = {"1": "@To Do", "2": "@Waiting For", "3": "@To Read",
                  "4": "Archive", "9": "Delete"}


def load_items():
    with open(BUILD / "triage_output.json") as f:
        t = json.load(f)
    SESSION["items"] = [i for i in t["items"] if i["spoken"]]
    SESSION["totals"] = (t["total"], t["silent_count"], t["spoken_count"])
    SESSION["gmail"], SESSION["labels"] = connect_gmail()
    return t


def keypad_twiml(idx):
    from xml.sax.saxutils import escape
    v = "Polly.Danielle-Generative"
    items = SESSION["items"]

    if idx == 0:
        total, silent, spoken = SESSION.get("totals", (0, 0, 0))
        intro = (f"Morning. You got {total} overnight. I filed {silent}. "
                 f"{spoken} need you.")
    else:
        intro = ""

    if idx >= len(items):
        return (f'<Response><Say voice="{v}">{intro} That is everything. '
                f'Everything else is filed.</Say></Response>')

    item = items[idx]
    gist = escape(item.get("gist", "")[:220])
    lead = ""
    if item.get("returned_to_you"):
        lead = (f"You had this in Waiting For since "
                f"{item['prior_since'][-2:].lstrip('0')}. They replied. ")
    body = (f"{intro} {lead}{gist} "
            f"Press 1 for to-do, 2 for waiting, 3 to read later, 4 to archive.")
    return (f'<Response><Gather numDigits="1" timeout="12" method="POST" '
            f'action="/keypad?i={idx + 1}">'
            f'<Say voice="{v}">{escape(body)}</Say></Gather>'
            f'<Redirect method="POST">/keypad?i={idx + 1}</Redirect></Response>')


@app.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    """Relay audio between the phone call and the model, both directions."""
    await ws.accept()
    load_env()
    SESSION["gmail"], SESSION["labels"] = connect_gmail()

    async with websockets.connect(
        f"wss://api.openai.com/v1/realtime?model={MODEL}",
        additional_headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
    ) as oai:
        await oai.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": MODEL,
                "output_modalities": ["audio"],
                "audio": {
                    # pcmu is G.711 u-law, which is what a phone line carries.
                    "input": {
                        "format": {"type": "audio/pcmu"},
                        # Tuned for a loud room: a high threshold ignores
                        # background chatter, and the long silence_duration
                        # stops it cutting in while he is still thinking.
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": VAD_THRESHOLD,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": VAD_SILENCE_MS,
                        },
                        "transcription": {"model": "whisper-1"},
                    },
                    "output": {"format": {"type": "audio/pcmu"},
                               "voice": VOICE},
                },
                "instructions": briefing(),
                "tools": [FILE_EMAIL_TOOL],
                "tool_choice": "auto",
            },
        }))
        # Nudge it to speak first -- otherwise it waits for him, and the call
        # opens with silence.
        await oai.send(json.dumps({"type": "response.create"}))

        stream_sid = {"v": None}

        async def phone_to_model():
            try:
                async for raw in ws.iter_text():
                    data = json.loads(raw)
                    if data["event"] == "start":
                        stream_sid["v"] = data["start"]["streamSid"]
                    elif data["event"] == "media":
                        await oai.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data["media"]["payload"],
                        }))
            except WebSocketDisconnect:
                pass

        async def model_to_phone():
            async for raw in oai:
                ev = json.loads(raw)
                t = ev.get("type", "")
                # Audio deltas are constant; everything else is worth seeing.
                if not t.endswith(".delta") and t != "response.output_audio.done":
                    print(f"  [oai] {t}")
                if t == "error":
                    print(f"  [oai ERROR] {json.dumps(ev)[:400]}")
                if t == "conversation.item.input_audio_transcription.completed":
                    print(f"  HUNG SAID: {ev.get('transcript','').strip()}")
                if ev.get("type") == "response.output_audio.delta" \
                        and ev.get("delta") and stream_sid["v"]:
                    await ws.send_json({
                        "event": "media",
                        "streamSid": stream_sid["v"],
                        "media": {"payload": ev["delta"]},
                    })
                elif ev.get("type") == \
                        "response.function_call_arguments.done":
                    await handle_file_email(oai, ev)
                elif ev.get("type") == "response.done":
                    log_transcript(ev)

        await asyncio.gather(phone_to_model(), model_to_phone())


async def handle_file_email(oai, ev):
    """The model decided; make it real.

    Applies the Gmail label and records the decision so the NEXT call can say
    "you filed this on the 12th". Without this the call is just narration.
    """
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
