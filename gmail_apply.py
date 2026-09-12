"""
Apply GTD labels in Gmail.

The only part of the system that writes anything. It writes LABELS and nothing
else: no send, no delete, no forward, no trash. That boundary is enforced here
by there being no code to cross it.

No-ops safely when credentials are absent, so the voice demo runs with or
without Gmail wired up.
"""
import os
from pathlib import Path

BUILD = Path(__file__).parent
CREDENTIALS = BUILD.parent / "credentials.json"
TOKEN = BUILD.parent / "token.json"

# modify is required to change labels. Not 'readonly' (cannot write) and
# deliberately not 'full' (which would permit deletion).
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

GTD_LABELS = ["@To Do", "@Waiting For", "@To Read"]


def available():
    return CREDENTIALS.exists() or TOKEN.exists()


def get_service():
    """Authorise and return a Gmail client, or None if not set up."""
    if not available():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS), SCOPES)
            # Fixed port so the redirect URI is predictable under WSL, where
            # the browser may not launch on its own.
            creds = flow.run_local_server(port=8765, open_browser=False)
        TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def ensure_labels(service):
    """Create the GTD labels if this inbox does not have them yet."""
    existing = service.users().labels().list(userId="me").execute()
    by_name = {l["name"]: l["id"] for l in existing.get("labels", [])}
    for name in GTD_LABELS:
        if name not in by_name:
            created = service.users().labels().create(
                userId="me",
                body={"name": name,
                      "labelListVisibility": "labelShow",
                      "messageListVisibility": "show"},
            ).execute()
            by_name[name] = created["id"]
    return by_name


def list_inbox(service, limit=20):
    """Read the inbox. Used to confirm the demo emails actually arrived."""
    res = service.users().messages().list(
        userId="me", labelIds=["INBOX"], maxResults=limit).execute()
    out = []
    for m in res.get("messages", []):
        full = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"]).execute()
        hdrs = {h["name"]: h["value"]
                for h in full["payload"].get("headers", [])}
        out.append({
            "gmail_id": m["id"],
            "subject": hdrs.get("Subject", ""),
            "from": hdrs.get("From", ""),
            "date": hdrs.get("Date", ""),
        })
    return out


def apply_bucket(service, gmail_id, bucket, label_ids):
    """Move one message into one GTD bucket.

    Removes the other GTD labels so an item cannot sit in two buckets at once
    -- moving from Waiting For to To Do has to actually move it, or the next
    call reads stale state.
    """
    add, remove = [], []
    for name, lid in label_ids.items():
        if name == bucket:
            add.append(lid)
        elif name in GTD_LABELS:
            remove.append(lid)

    if bucket in ("Archive", "Delete"):
        # Archive means: out of the inbox, still in All Mail. Delete is treated
        # the same way on purpose -- this agent never destroys mail.
        remove.append("INBOX")

    body = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove
    if not body:
        return None
    return service.users().messages().modify(
        userId="me", id=gmail_id, body=body).execute()


if __name__ == "__main__":
    import json
    import sys

    if not available():
        print("No credentials.json found. Gmail is not wired up yet.")
        print(f"Expected at: {CREDENTIALS}")
        sys.exit(1)

    svc = get_service()
    labels = ensure_labels(svc)
    print(f"Labels ready: {', '.join(GTD_LABELS)}\n")

    msgs = list_inbox(svc)
    print(f"Inbox has {len(msgs)} messages:")
    for m in msgs:
        print(f"  - {m['subject'][:60]}")

    if "--diff" in sys.argv:
        fx = json.load(open(BUILD / "fixtures" / "inbox.json"))["messages"]
        want = {m["subject"] for m in fx}
        have = {m["subject"] for m in msgs}
        print(f"\nIn fixture but NOT in inbox ({len(want - have)}):")
        for s in sorted(want - have):
            print(f"  ! {s}")
