#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tick.py -- the supervisor. Run it on a timer; it is what makes silence safe.

  python tick.py                      # one pass: collect replies, then re-ping what is due
  python tick.py --transport stdout   # dry run, nothing leaves the machine

One pass does two things:
  1. fetch replies from the transport -> feed them to `approval_gate.py check`
  2. run `approval_gate.py due` -> post each returned envelope to targets["primary"]

Schedule it every 5 minutes (cron / Task Scheduler / systemd timer). Without it, an
unanswered question sits forever and your agent has quietly become a hang.

IMPORTANT -- do not run this inside the agent it supervises. If the agent dies, its
questions must still be re-pinged and eventually retired; a supervisor sharing the
process is a supervisor that dies with the thing it watches.
"""

import os
import sys
import json
import subprocess
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "approval_gate.py")
CONFIG = os.environ.get("APPROVAL_CONFIG", os.path.join(HERE, "approval.json"))


def _load_transport(name):
    path = os.path.join(HERE, "transports", "%s.py" % {
        "telegram": "telegram_bot", "stdout": "stdout_transport"}.get(name, name))
    if not os.path.exists(path):
        sys.stderr.write("[tick] no transport at %s\n" % path)
        sys.exit(2)
    spec = importlib.util.spec_from_file_location("transport", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gate(args, stdin=None):
    return subprocess.run([sys.executable, GATE] + args, input=stdin,
                          capture_output=True, text=True, encoding="utf-8")


def main():
    name = "telegram"
    if "--transport" in sys.argv:
        name = sys.argv[sys.argv.index("--transport") + 1]
    t = _load_transport(name)

    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        sys.stderr.write("[tick] no config at %s -- cp approval.example.json approval.json\n" % CONFIG)
        sys.exit(2)
    except json.JSONDecodeError as e:
        sys.stderr.write("[tick] config is not valid JSON: %s\n" % e)
        sys.exit(2)
    primary = (cfg.get("targets") or {}).get("primary")
    if not primary:
        sys.stderr.write("[tick] config has no targets.primary\n")
        sys.exit(2)

    # 1. replies in
    replies = t.fetch()
    if replies:
        r = _gate(["check"], stdin=json.dumps(replies, ensure_ascii=False))
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        # ACK immediately, in the same channel. A human who answers into silence stops
        # answering; the receipt costs one message and buys every future reply.
        for line in r.stdout.splitlines():
            if line.startswith("ACK:"):
                t.post(line, primary)

    # 2. overdue out
    d = _gate(["due"])
    if d.returncode != 0:
        sys.stderr.write(d.stderr)
        sys.exit(d.returncode)
    try:
        due = json.loads(d.stdout or "{}").get("due", [])
    except json.JSONDecodeError:
        sys.stderr.write("[tick] could not parse `due` output:\n%s\n" % d.stdout[:400])
        sys.exit(1)
    for item in due:
        t.post(item["envelope"], primary)
    print("[tick] replies=%d reping=%d" % (len(replies), len(due)))


if __name__ == "__main__":
    main()
