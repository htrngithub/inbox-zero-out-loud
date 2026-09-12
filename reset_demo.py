"""
Put the inbox back to its pre-call state.

A demo gets recorded more than once. This strips the GTD labels the last take
applied and restores the prior-call memory, so every attempt starts from the
same place and "you've had this since the 3rd" is true each time.
"""
import json
import shutil
from pathlib import Path

import gmail_apply

BUILD = Path(__file__).parent

# What the agent should believe happened on previous calls. The contract review
# is the item that comes back to him -- it was filed as Waiting For, and the
# counterparty has since replied.
SEED_STATE = {
    "calls": [{"call_id": "call-2026-09-03-0710",
               "date": "2026-09-03T07:10:00-07:00",
               "items_spoken": 3, "items_silent": 9}],
    "decisions": [
        {"thread_key": "contract review",
         "subject": "Contract review — draft attached",
         "correspondent": "Marcus Webb", "bucket": "@Waiting For",
         "decided_on": "2026-09-03T07:12:00-07:00", "decided_by": "hung",
         "note": "Sent the draft back to Marcus, waiting on his read."},
        {"thread_key": "kitchen quote", "subject": "Kitchen quote",
         "correspondent": "Dave Rinaldi", "bucket": "@Waiting For",
         "decided_on": "2026-09-08T07:05:00-07:00", "decided_by": "hung",
         "note": "Asked Dave for revised numbers."},
    ],
}


def main():
    (BUILD / "state" / "state.json").write_text(
        json.dumps(SEED_STATE, indent=2))
    print("Prior-call memory restored.")

    for f in ("call_decisions.json", "triage_output.json"):
        p = BUILD / f
        if p.exists():
            p.unlink()
    print("Last call's output cleared.")

    if not gmail_apply.available():
        print("Gmail not connected; labels left alone.")
        return

    svc = gmail_apply.get_service()
    labels = gmail_apply.ensure_labels(svc)
    gtd = [labels[n] for n in gmail_apply.GTD_LABELS if n in labels]
    cleared = 0
    for name in gmail_apply.GTD_LABELS:
        lid = labels.get(name)
        if not lid:
            continue
        res = svc.users().messages().list(
            userId="me", labelIds=[lid], maxResults=50).execute()
        for m in res.get("messages", []):
            svc.users().messages().modify(
                userId="me", id=m["id"],
                body={"removeLabelIds": gtd, "addLabelIds": ["INBOX"]}
            ).execute()
            cleared += 1
    print(f"Cleared GTD labels from {cleared} message(s); all back in the inbox.")
    print("\nReady for a take.")


if __name__ == "__main__":
    main()
