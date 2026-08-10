#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stdout_transport.py -- the transport you should use on day one.

`post` prints the envelope instead of sending it. `fetch` reads reply dicts from a file
you edit by hand ($APPROVAL_FAKE_REPLIES, default ./fake_replies.json).

This is not only a test double. Run the gate in shadow for a few days with this transport:
every ask lands in your log instead of in a person's notifications, and you find out how
chatty your classification really is before anyone has to live with it.

  python transports/stdout_transport.py post "hello" -1000000000001
  echo '[{"channel":"telegram","sender_id":"100000001","text":"+"}]' > fake_replies.json
  python transports/stdout_transport.py fetch | python approval_gate.py check
"""

import os
import sys
import json

REPLIES = os.environ.get("APPROVAL_FAKE_REPLIES", "fake_replies.json")


def post(text, target):
    print("---- would send to %s ----\n%s\n--------" % (target, text))
    return True


def fetch():
    try:
        with open(REPLIES, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    return data if isinstance(data, list) else [data]


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "post" and len(a) >= 3:
        post(" ".join(a[1:-1]), a[-1])
    elif a and a[0] == "fetch":
        print(json.dumps(fetch(), ensure_ascii=False))
    else:
        print(__doc__)
