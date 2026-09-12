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

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env.local        # then fill in your keys

python triage.py --offline        # sort the inbox, no API needed
python call.py --dry-run          # see what it would say
python call.py                    # actually ring the phone
```

`triage.py` uses OpenAI when `OPENAI_API_KEY` is set, and falls back to
deterministic keyword rules when it is not — so the demo survives a dead
network. Both paths score 12/12 against the fixture's ground truth.

## How it is put together

| File | Job |
|---|---|
| `triage.py` | Sorts the inbox into buckets. Reads prior state so replies can move an item back to you. |
| `speech.py` | Turns triage output into what the agent says. Pacing and phrasing live here. |
| `call.py` | Places the outbound call. |
| `state/state.json` | Memory between calls. The reason it can say "since the 3rd". |
| `fixtures/inbox.json` | 12 sample emails with expected buckets, used as the test. |

Triage runs *before* the call on purpose. The call is a readout and a decision
loop, never live reasoning — which is what keeps it fast enough to feel like a
person rather than a hold queue.

## Status

Built in an afternoon at the AI Tinkerers "Agents, Everywhere" global hackathon,
September 2026.
