"""
A terminal view of the inbox that updates as the call files things.

Gmail in a browser is the better shot, but it depends on a browser, a login
and a screen recorder behaving. This needs none of them: it runs in the
terminal that is already on screen, and the labels visibly move as they are
spoken. A fallback for the demo, and genuinely useful while debugging a call.
"""
import json
import os
import sys
import time
from pathlib import Path

BUILD = Path(__file__).parent
COLOR = {
    "@To Do": "\033[1;32m",
    "@Waiting For": "\033[1;33m",
    "@To Read": "\033[1;36m",
    "Archive": "\033[0;90m",
    "Delete": "\033[0;31m",
}
RESET = "\033[0m"


def load():
    with open(BUILD / "triage_output.json") as f:
        t = json.load(f)
    try:
        with open(BUILD / "call_decisions.json") as f:
            decided = {d["id"]: d for d in json.load(f)}
    except Exception:
        decided = {}
    return t, decided


def render(t, decided):
    os.system("clear")
    print(f"\n  \033[1mINBOX\033[0m   {t['total']} overnight  "
          f"·  {t['silent_count']} filed  ·  {t['spoken_count']} need you\n")
    for i in t["items"]:
        d = decided.get(i["id"])
        bucket = d["bucket"] if d else i["bucket"]
        col = COLOR.get(bucket, "")
        mark = "\033[1;32m<-- just now\033[0m" if d else ""
        star = "*" if i["spoken"] else " "
        print(f"   {star} {col}{bucket:<14}{RESET} "
              f"{i['subject'][:46]:<48} {mark}")
    print(f"\n   * = spoken on the call\n")


def main():
    last = None
    while True:
        try:
            t, decided = load()
        except Exception:
            time.sleep(1)
            continue
        key = json.dumps(sorted((k, v["bucket"]) for k, v in decided.items()))
        if key != last:
            render(t, decided)
            last = key
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
