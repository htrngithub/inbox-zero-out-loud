"""
Place the morning call.

Tier 3 of three: an outbound call with inline TwiML. No webhook, no tunnel,
no public URL -- which is exactly why it is the floor. This is the path that
was proven working the night before; if everything else fails, this still
rings the phone and reads the four items that need him.
"""
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from speech import build_script, plain_text

BUILD = Path(__file__).parent
VOICE = os.environ.get("VOICE", "Polly.Danielle-Generative")


def load_env():
    """Read .env.local from the parent dir. Never committed, never printed."""
    env_path = BUILD.parent / ".env.local"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def twiml(ssml):
    return f'<Response><Say voice="{VOICE}">{ssml}</Say></Response>'


def tunnel_url():
    """Ask the local ngrok agent for its public URL, if it is running."""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:4040/api/tunnels", timeout=3) as r:
            import json
            tunnels = json.loads(r.read()).get("tunnels", [])
        for t in tunnels:
            if t.get("public_url", "").startswith("https://"):
                return t["public_url"]
    except Exception:
        pass
    return None


def place_call(ssml, sid, token, frm, to, url_base=None):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    if url_base:
        # Two-way: Twilio fetches each turn from our server, so it can listen.
        endpoint = os.environ.get("CALL_ENDPOINT", "/call")
        params = {"To": to, "From": frm, "Url": f"{url_base}{endpoint}"}
    else:
        # One-way floor: the whole script goes out with the call itself.
        params = {"To": to, "From": frm, "Twiml": twiml(ssml)}
    data = urllib.parse.urlencode(params).encode()

    import base64
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        import json
        return json.loads(r.read())


def main():
    load_env()
    ssml, spoken = build_script()

    if "--dry-run" in sys.argv:
        print("=== DRY RUN, no call placed ===\n")
        print(plain_text(ssml))
        print(f"\n{len(spoken)} items would be spoken.")
        return

    sid = os.environ.get("TW_SID")
    token = os.environ.get("TW_TOKEN")
    frm = os.environ.get("TW_FROM")
    to = os.environ.get("TW_TO")

    missing = [n for n, v in
               [("TW_SID", sid), ("TW_TOKEN", token),
                ("TW_FROM", frm), ("TW_TO", to)] if not v]
    if missing:
        print(f"Missing credentials: {', '.join(missing)}", file=sys.stderr)
        print("Put them in ~/hackathon/.env.local", file=sys.stderr)
        sys.exit(1)

    # The gotcha that cost ten minutes the night before.
    if len(token) != 32:
        print(f"TW_TOKEN is {len(token)} chars, expected 32. "
              "A short paste 401s with code 20003.", file=sys.stderr)
        sys.exit(1)

    url_base = None if "--one-way" in sys.argv else tunnel_url()
    if url_base:
        print(f"Two-way call via {url_base}")
        print("  (the call server must be running on the tunnelled port)")
    else:
        print("One-way call: reads the list, does not listen.")

    print(f"Calling {to} from {frm}...")
    r = place_call(ssml, sid, token, frm, to, url_base)
    print(f"Call {r.get('sid')} -> status {r.get('status')}")


if __name__ == "__main__":
    main()
