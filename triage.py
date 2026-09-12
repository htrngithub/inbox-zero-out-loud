"""
Triage the inbox into GTD buckets, before the call.

The call is a readout, not live reasoning -- so everything expensive happens
here and lands in triage_output.json. This is what keeps the phone call fast
enough to not look broken on camera.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

BUILD = Path(__file__).parent
BUCKETS = ["@To Do", "@Waiting For", "@To Read", "Archive", "Delete"]

# The buckets that need Hung's voice on the call. Everything else is silent.
SPOKEN_BUCKETS = ["@To Do"]

TRIAGE_SYSTEM_PROMPT = """You triage email into a GTD system that the user has run for years.

The five buckets, and what belongs in each:
- "@To Do": needs an action FROM THE USER. Either a reply he must write, or a
  concrete errand he must run. A thing only he can move forward.
- "@Waiting For": he is blocked on someone ELSE. He has already done his part.
- "@To Read": newsletters, digests, long reads. Never a To Do, no matter how
  interesting. Reading is not an action.
- "Archive": FYI only. Real mail, no action, worth keeping.
- "Delete": marketing and junk.

Rules that matter:
- A reply needed and an errand needed are BOTH "@To Do". Distinguish them in
  your reason, not your bucket.
- If the user was waiting on someone and that person has NOW REPLIED, the item
  moves OUT of "@Waiting For" and back to "@To Do". This is the most important
  transition in the system.
- Prefer being honest over being decisive. If an email is genuinely ambiguous,
  say so in your reason and pick the more conservative bucket.

Return ONLY valid JSON, no markdown fence:
{"items": [{"id": "...", "bucket": "...", "reason": "<one short clause>",
            "gist": "<what the email actually SAYS, spoken aloud>",
            "decision": "<the question he has to answer, or null>",
            "confident": true}]}

"gist" is the important field. It is read to him on a phone call and it is all
he gets -- he cannot see the email. Give him the SUBSTANCE, not the topic:
the number, the date, the ask, the constraint. One or two sentences, the way
you would tell a colleague across a desk.

  BAD:  "Dave replied about the kitchen quote."      (tells him nothing)
  GOOD: "Dave's revised quote came back about $1,400 over -- cabinets came
         down, electrical went up once he saw the panel. He needs an answer
         by Monday to hold the installer."

"decision" is the single question he must answer, phrased as a question, or
null if the item needs an action but no judgement. Keep it under ten words.
"""


def load_inbox(path):
    with open(path) as f:
        return json.load(f)["messages"]


def load_state(path):
    if not os.path.exists(path):
        return {"calls": [], "decisions": []}
    with open(path) as f:
        return json.load(f)


def prior_decision_for(message, state):
    """Find a previous decision about this thread.

    Matching is deliberately loose -- subjects mutate as threads go back and
    forth ("Contract review" becomes "Re: Contract review -- any update?"), so
    we match on a stored thread_key appearing in the subject, plus the sender.
    """
    subject = message["subject"].lower()
    sender = message["from"].lower()
    for d in state.get("decisions", []):
        key = d["thread_key"].lower()
        if key in subject:
            return d
        last_name = d["correspondent"].split()[-1].lower()
        if last_name in sender and any(w in subject for w in key.split()):
            return d
    return None


def build_prompt(messages, state):
    """Give the model the inbox plus what it already decided on past calls."""
    lines = []
    for m in messages:
        prior = prior_decision_for(m, state)
        block = (
            f"---\nid: {m['id']}\nfrom: {m['from']}\n"
            f"subject: {m['subject']}\ndate: {m['date']}\n"
            f"body: {m['body']}"
        )
        if prior:
            block += (
                f"\nPRIOR STATE: on {prior['decided_on'][:10]} you filed this "
                f"thread as \"{prior['bucket']}\" -- {prior['note']} "
                f"Consider whether this new message changes that."
            )
        lines.append(block)
    return "\n".join(lines)


def triage_with_openai(messages, state, api_key):
    import urllib.request

    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(messages, state)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        body = json.loads(r.read())
    return json.loads(body["choices"][0]["message"]["content"])["items"]


def triage_offline(messages, state):
    """Deterministic fallback so the demo survives a dead API or dead wifi.

    Keyword rules, not intelligence. Good enough to keep the voice loop alive
    at 12:00 if OpenAI is unreachable from a saturated venue network.
    """
    items = []
    for m in messages:
        subj = m["subject"].lower()
        body = m["body"].lower()
        sender = m["from"].lower()
        blob = f"{subj} {body}"

        if any(w in blob for w in ["% off", "sale", "shop now", "deals"]):
            bucket, reason = "Delete", "marketing"
        elif any(w in subj for w in ["digest", "issue", "weekly", "newsletter"]):
            bucket, reason = "@To Read", "newsletter"
        elif any(w in blob for w in ["statement is ready", "order shipped",
                                     "security alert", "reminder:", "no action"]):
            bucket, reason = "Archive", "FYI only"
        elif any(w in blob for w in ["ready for pickup", "available",
                                     "confirm", "let me know", "any update",
                                     "are we still", "decide"]):
            bucket, reason = "@To Do", "needs an action from you"
        else:
            bucket, reason = "Archive", "no action detected"

        prior = prior_decision_for(m, state)
        if prior and prior["bucket"] == "@Waiting For" and bucket != "Delete":
            bucket = "@To Do"
            reason = f"they replied; was Waiting For since {prior['decided_on'][:10]}"

        items.append({
            "id": m["id"],
            "bucket": bucket,
            "reason": reason,
            "gist": m["body"][:220],
            "decision": None,
            "confident": True,
        })
    return items


def enrich(items, messages, state):
    """Attach the email and any prior decision to each triage result."""
    by_id = {m["id"]: m for m in messages}
    out = []
    for it in items:
        m = by_id.get(it["id"])
        if not m:
            continue
        prior = prior_decision_for(m, state)
        it["subject"] = m["subject"]
        it["from"] = m["from"]
        it.setdefault("gist", m["body"][:220])
        it.setdefault("decision", None)
        it["spoken"] = it["bucket"] in SPOKEN_BUCKETS
        if prior:
            it["prior_bucket"] = prior["bucket"]
            it["prior_since"] = prior["decided_on"][:10]
            it["returned_to_you"] = (
                prior["bucket"] == "@Waiting For" and it["bucket"] == "@To Do"
            )
        else:
            it["returned_to_you"] = False
        out.append(it)
    # Items that came back to him lead the call -- that is the money moment.
    # Within those, the longest-waiting goes first: "since the 3rd" lands
    # harder than "since the 8th", and it is the one that proves the memory.
    def order(x):
        since = x.get("prior_since", "9999-99-99")
        return (not x["returned_to_you"], since, x["id"])

    out.sort(key=order)
    return out


def main():
    inbox_path = BUILD / "fixtures" / "inbox.json"
    state_path = BUILD / "state" / "state.json"
    messages = load_inbox(inbox_path)
    state = load_state(state_path)

    api_key = os.environ.get("OPENAI_API_KEY")
    mode = "offline"
    if api_key and "--offline" not in sys.argv:
        try:
            items = triage_with_openai(messages, state, api_key)
            mode = "openai"
        except Exception as e:
            print(f"  ! OpenAI triage failed ({e}); using offline rules.",
                  file=sys.stderr)
            items = triage_offline(messages, state)
    else:
        items = triage_offline(messages, state)

    items = enrich(items, messages, state)
    spoken = [i for i in items if i["spoken"]]

    out = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": mode,
        "total": len(items),
        "spoken_count": len(spoken),
        "silent_count": len(items) - len(spoken),
        "items": items,
    }
    out_path = BUILD / "triage_output.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)

    print(f"Triaged {out['total']} emails via {mode}.")
    print(f"  {out['spoken_count']} need you, {out['silent_count']} handled silently.\n")
    for i in items:
        mark = "SPEAK " if i["spoken"] else "silent"
        flag = "  <-- came back to you" if i.get("returned_to_you") else ""
        print(f"  [{mark}] {i['bucket']:<14} {i['subject'][:48]:<50} ({i['reason']}){flag}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
