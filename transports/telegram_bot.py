#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""telegram_bot.py -- reference transport for approval_gate.py. Bot API, stdlib only.

  export APPROVAL_TG_TOKEN=123456:AA...        # from @BotFather
  python transports/telegram_bot.py post "<text>" <chat_id>
  python transports/telegram_bot.py fetch      > replies.json
  python approval_gate.py check < replies.json

`fetch` uses getUpdates with a persisted offset, so each reply is returned exactly once.
Put the bot in the sterile approval chat and nowhere else.

Why a bot and not your user account: a bot cannot read anything you do not send it, so a
compromised approval bot leaks only approval traffic. The tradeoff is that the human must
address the bot's chat -- which is what you want anyway (see docs/GOTCHAS.md #2).
"""

import os
import sys
import json
import urllib.request
import urllib.parse
import urllib.error

TOKEN = os.environ.get("APPROVAL_TG_TOKEN", "")
API = "https://api.telegram.org/bot%s/%s"
OFFSET_FILE = os.environ.get("APPROVAL_TG_OFFSET",
                             os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          ".tg_offset"))


def _call(method, params, timeout=35):
    if not TOKEN:
        sys.stderr.write("[telegram] set APPROVAL_TG_TOKEN\n")
        sys.exit(2)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(API % (TOKEN, method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        sys.stderr.write("[telegram] HTTP %s: %s\n" % (e.code, body[:300]))
        return {"ok": False}
    except Exception as e:
        sys.stderr.write("[telegram] %s\n" % e)
        return {"ok": False}


def post(text, chat_id):
    r = _call("sendMessage", {"chat_id": chat_id, "text": text,
                              "disable_web_page_preview": "true"})
    if not r.get("ok"):
        return None
    return r["result"]["message_id"]


def _offset(new=None):
    if new is not None:
        try:
            with open(OFFSET_FILE, "w") as f:
                f.write(str(new))
        except Exception:
            pass
        return new
    try:
        with open(OFFSET_FILE) as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def fetch():
    """Return reply dicts in the shape approval_gate.py `check` expects."""
    off = _offset()
    r = _call("getUpdates", {"offset": off, "timeout": 0, "allowed_updates": '["message"]'})
    out = []
    if not r.get("ok"):
        return out
    last = off
    for u in r.get("result", []):
        last = max(last, u.get("update_id", 0) + 1)
        m = u.get("message") or {}
        txt = m.get("text")
        if not txt:
            continue
        frm = m.get("from") or {}
        if frm.get("is_bot"):
            continue  # our own envelopes come back through getUpdates in groups
        reply_to = ((m.get("reply_to_message") or {}).get("text") or "")
        out.append({
            "channel": "telegram",
            # The numeric user id is the identity. `username` is display text a stranger
            # can change to anything; it must never authorize.
            "sender_id": str(frm.get("id") or ""),
            "text": txt,
            "reply_to_text": reply_to,
        })
    _offset(last)
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "post" and len(a) >= 3:
        mid = post(" ".join(a[1:-1]), a[-1])
        print(json.dumps({"sent": bool(mid), "message_id": mid}))
    elif a and a[0] == "fetch":
        print(json.dumps(fetch(), ensure_ascii=False))
    else:
        print(__doc__)
