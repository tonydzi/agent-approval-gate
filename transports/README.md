# Transports

`approval_gate.py` never touches the network. That is not laziness — it is what makes the
gate testable, auditable and portable. The engine speaks two shapes:

**Out** — you post `ask_text` (from `ask`) or `envelope` (from `due`) wherever your human is.

**In** — you feed `check` a JSON list on stdin:

```json
[
  {
    "channel": "telegram",
    "sender_id": "100000001",
    "text": "+",
    "reply_to_text": "[agent-01] approval needed #a1b2 ... [#a1b2]"
  }
]
```

| field | required | meaning |
|---|---|---|
| `channel` | yes | must match a key under an approver's `ids` in your config |
| `sender_id` | yes | the platform's identity of the sender. **This is the authorization.** |
| `text` | yes | the raw reply |
| `reply_to_text` | no | the message being replied to. Supply it if your platform has replies — it is how a late answer still lands on the right question. |

Never synthesize `sender_id` from the message body. If your platform gives you a display
name that a stranger can set to "Alex Rivera", that field is not an identity.

## What ships here

- **`telegram_bot.py`** — the default reference transport. Bot API over `urllib`, no
  dependencies. ~120 lines; read it, it is the whole contract.
- **`stdout_transport.py`** — prints instead of sending, and reads replies from a file.
  Use it in tests, in CI, and on the first day when you do not trust the gate yet.

## Writing your own

Two functions:

```python
def post(text, target): ...          # -> anything truthy on success
def fetch(since_token=None): ...     # -> (list_of_reply_dicts, new_since_token)
```

Notes per platform, from having done this:

- **Slack** — `sender_id` is the `user` field (`U…`), not `username`. Use `thread_ts` for
  binding instead of `reply_to_text`; map it to the question id yourself and pass `#id`
  into `text`, or keep a `ts -> id` map.
- **Discord** — `sender_id` is `author.id`. Beware webhooks: a webhook message has an
  `author` you do not control. Ignore messages where `author.bot` is true.
- **Email** — the `From:` header is not an identity. Either check DKIM/ARC on your side
  before building the reply dict, or do not use email as an approval channel.
- **SMS** — the engine compares the last 10 digits, so formatting differences are fine.
  SIM-swap is a real threat model for class E; see [`../docs/SECURITY.md`](../docs/SECURITY.md).
