# Inbox Zero, Out Loud

**An agent that triages your inbox overnight, then phones you and reads only what actually needs you.**

Every other agent waits in an app. This one calls you while you make coffee,
tells you what needs a decision, and files the rest without asking.

## Why a phone call

The decision was never the hard part. Opening the laptop was.

Triage is the ideal voice job because every decision is one word long — "todo",
"waiting", "archive" — and it is the one kind of work that fits in the gaps of a
morning. An agent that lives in an app still requires you to go to it, which is
the exact step you have been deferring. So this one initiates.

## What it does

1. **Sorts the inbox** into five GTD buckets: `@To Do`, `@Waiting For`,
   `@To Read`, `Archive`, `Delete`.
2. **Calls you** and speaks only the items in `@To Do`. Everything else is
   filed silently. *The silence is the product.*
3. **Remembers the last call.** Decisions are written to `state/state.json`, so
   the next call knows what happened on the previous one.

That third part is the one a stateless assistant cannot fake:

> "Marcus Webb, on the contract review. You've had this in Waiting For since the
> 3rd. They've replied, so it's back on you."

It knows that because it filed it there on a previous call.

## What it deliberately will not do

**It labels. It never sends, deletes, or forwards.**

This is a designed boundary, not a missing feature. The agent proposes and
applies organisation; anything that reaches another human stays in your hands.
An agent that refuses to act without you is the point.

The boundary is enforced by there being no code to cross it: `gmail_apply.py`
is the only module that writes, it holds the `gmail.modify` scope rather than
`gmail.full`, and it can only add and remove labels. "Archive" and "Delete"
both simply leave the inbox — nothing is ever destroyed.

It also refuses to act on a decision it did not clearly hear. On a phone in a
loud room, speech recognition returns confident nonsense; the agent asks you to
repeat rather than filing a guess.

## Running it

### The short version, no accounts needed

```bash
pip install -r requirements.txt
python triage.py --offline        # sort the fixture inbox, no API key
python call.py --dry-run          # print what the agent would say
```

That runs the whole triage and shows you the script, without a phone, an
inbox, or a key.

### The full version — the one in the video

```bash
cp .env.example .env.local        # Twilio + OpenAI keys go here
```

You also need:

1. **A public URL for your machine**, so Twilio can stream call audio to it:
   ```bash
   ngrok http 5055
   ```
2. **Gmail credentials** (optional — it falls back to the fixture without
   them). Create an OAuth *desktop* client in Google Cloud with the Gmail API
   enabled, save it as `credentials.json` one level above this directory, then:
   ```bash
   python gmail_apply.py --diff    # authorises, and checks what is in the inbox
   ```

Then:

```bash
python triage.py --live           # read the real inbox and sort it
python realtime_server.py         # the speech-to-speech call server
python call.py                    # ring the phone
```

`demo.sh` does the last three as one command.

### Three voice paths, in order of preference

The call degrades rather than breaking, which matters on venue wifi:

| Path | What it is | Needs |
|---|---|---|
| `realtime_server.py` | Speech-to-speech, interruptible. The demo. | ngrok + OpenAI Realtime |
| `server.py` | Generated speech + speech recognition, one turn at a time | ngrok |
| `call.py --one-way` | Reads the list and hangs up | nothing but Twilio |

Likewise for triage: `triage.py` uses OpenAI when `OPENAI_API_KEY` is set and
falls back to deterministic keyword rules when it is not. Both paths agree with
the fixture's expected buckets on all 12 messages.

## How it is put together

| File | Job |
|---|---|
| `triage.py` | Sorts the inbox into buckets. Reads prior state so replies can move an item back to you. |
| `realtime_server.py` | The call itself: streams audio to a speech-to-speech model, and turns what you say into filed email. |
| `gmail_apply.py` | The only code that writes anything. Labels, and nothing else. |
| `speech.py` | Turns triage output into what the agent says, for the generated-speech path. |
| `server.py` | The fallback call: generated speech plus speech recognition. |
| `call.py` | Places the outbound call and picks the best available path. |
| `state/state.json` | Memory between calls. The reason it can say "since the 3rd". |
| `fixtures/inbox.json` | 12 sample emails with expected buckets, used as the test. |
| `show_inbox.py` | A terminal view of the inbox that updates as the call files things. |
| `reset_demo.py` | Puts the inbox and the memory back, so a demo can be run twice. |

Triage runs *before* the call on purpose. The call is a readout and a decision
loop, never live reasoning — which is what keeps it fast enough to feel like a
person rather than a hold queue.

## Status

Built in an afternoon at the AI Tinkerers "Agents, Everywhere" global hackathon,
September 2026.
