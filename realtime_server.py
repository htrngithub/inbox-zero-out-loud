"""
The same call, run through a speech-to-speech model.

Tier 1 of three. Twilio holds the phone call and streams the raw audio to
OpenAI's Realtime API, which does both the talking and the listening. That is
what removes the read-aloud quality of text-to-speech -- it is not a better
voice setting, it is a different kind of model.

Tiers 2 and 3 (server.py, call.py) still work and are the fallback.
"""
import asyncio
import base64
import json
import os
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

app = FastAPI()
SESSION = {"items": [], "gmail": None, "labels": {}, "decisions": []}


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
        line = f'{n}. From {who}: "{item["subject"]}" -- {item.get("reason", "")}'
        if item.get("returned_to_you"):
            line += (f' IMPORTANT: he filed this under Waiting For on '
                     f'{item["prior_since"]}; they have now replied, so it is '
                     f'back on him. Say so.')
        lines.append(line)

    return f"""You are a morning inbox assistant, on the phone with your user.

SPEAK ENGLISH. Every word of this call is in English, always, no matter what
names appear below or what you think you hear. Never switch language.

You have ALREADY triaged his inbox. {t['total']} arrived overnight. You filed
{t['silent_count']} of them without needing him. {t['spoken_count']} need him.

Open the call like this, in your own words: greet him, say how many came in,
say how many you filed, say how many need him. Then go through the items below
ONE AT A TIME. After each one, stop and ask what he wants to do, then wait.

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
                    "input": {"format": {"type": "audio/pcmu"},
                              "turn_detection": {"type": "server_vad"}},
                    "output": {"format": {"type": "audio/pcmu"},
                               "voice": VOICE},
                },
                "instructions": briefing(),
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
                if ev.get("type") == "response.output_audio.delta" \
                        and ev.get("delta") and stream_sid["v"]:
                    await ws.send_json({
                        "event": "media",
                        "streamSid": stream_sid["v"],
                        "media": {"payload": ev["delta"]},
                    })
                elif ev.get("type") == "response.done":
                    log_transcript(ev)

        await asyncio.gather(phone_to_model(), model_to_phone())


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
