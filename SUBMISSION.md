# Submission — Inbox Zero, Out Loud

## Title
**Inbox Zero, Out Loud**

## One-liner
Every other agent waits in an app. This one calls you while you make coffee,
tells you what actually needs you, and files the rest without asking.

## Description

Most inboxes don't get away from you because triage is hard. They get away from
you because opening the laptop is a decision you keep deferring. So this agent
doesn't wait to be opened — it calls.

Overnight it reads the inbox and sorts it into the five GTD buckets I've run my
life on for years: To Do, Waiting For, To Read, Archive, Delete. In the morning
the phone rings. It tells me what came in, how much it already filed, and then
walks me through only the items that need a human. Everything else is gone
before I hear about it. **The silence is the product.**

It speaks through OpenAI's Realtime API, so it's a conversation rather than a
recording — I can interrupt it, ask "what does that one actually say?" and it
reads me the sender's own words before I rule. When I say "waiting for," the
label moves in Gmail while I'm still on the call.

And it remembers. Every decision is written down, so the next call knows what
happened on the last one: *"You'd been waiting on the contract review since the
3rd — they've replied, so it's back on you."* That sentence is only possible
because a previous call wrote it down. A stateless assistant cannot say it.

**What it will not do:** it never sends, never deletes, never forwards. It
labels, and that's all. Anything that reaches another human stays in my hands.
In a field of agents racing to act alone, an agent that refuses to act without
you is the point, not the limitation.

## Why the phone is the right place for this

Triage is the ideal voice job because every decision is one word long — "todo",
"waiting", "archive" — and it's the one kind of work that fits in the gaps of a
morning. Put it in an app and you've rebuilt the thing I was avoiding. Put it in
a phone call and it happens while I'm doing something else.

## How it's built

- **Triage runs before the call**, not during it, so the call is a readout and
  a decision loop rather than a model thinking out loud at me. That's what keeps
  it fast enough to feel like a person.
- **Twilio** holds the call and streams audio to **OpenAI Realtime** (`gpt-realtime`),
  which does the talking and the listening.
- **Gmail API**, scoped to `modify` — labels only. There is no send or delete
  path in the code.
- **State lives in a file** between calls. It's the cheapest part of the system
  and the most convincing.
- Falls back gracefully at every layer: no Realtime → generated speech; no
  tunnel → a one-way call; no OpenAI → deterministic rules; no Gmail → a local
  fixture. A demo that survives hackathon wifi.

## Honest limits

Much of this is a good prompt plus connectors, and that gap is closing. The
genuinely hard parts are the unprompted call and the state carried between
calls — those are what a chat window structurally can't do.

Speech recognition in a loud room is the real weakness. The agent refuses to
file anything it didn't clearly hear and asks again instead, which is the right
failure mode but not an invisible one.

## Stack
OpenAI Realtime API · OpenAI GPT-4o · Twilio Voice · Gmail API · Python

## Repo
https://github.com/htrngithub/inbox-zero-out-loud
