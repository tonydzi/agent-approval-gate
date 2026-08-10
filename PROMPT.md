# Wire the approval gate into your agent

Paste everything below into Claude Code, Codex, or whatever agent you build with. It will
install the gate, wire it into your agent's action path, and prove it works. Budget ~10
minutes, most of it spent answering two questions.

---

You are installing `agent-approval-gate` — a human-in-the-loop approval gate for an
autonomous agent — into this project. Work autonomously; ask me only the two questions in
Step 2.

**Source:** https://github.com/tonydzi/agent-approval-gate

## Step 1 — install

```bash
git clone https://github.com/tonydzi/agent-approval-gate.git .approval-gate
cd .approval-gate && python tests/test_gate.py
```

All tests must pass before you continue. It is standard library only — no pip install, no
API key. If Python is older than 3.8, stop and tell me.

## Step 2 — ask me exactly two things

1. **Where should approval questions go?** I need a channel that will contain *nothing
   but* these questions. If I name a busy channel, push back once and explain why: asks
   posted into a working channel get buried under other traffic within a day and stop
   being read. Then create/collect the channel id.
2. **Who may approve, and what is their platform user id?** Not their display name — the
   numeric/stable id. If I want deputies, collect all of them.

Then write `.approval-gate/approval.json` from `approval.example.json` with those values.
Leave the timing defaults alone for now.

## Step 3 — draw the class table for THIS project

Read `docs/DECISION-CLASSES.md`. Then go through the actions this agent can actually take
— read the codebase, do not guess — and produce a table in `docs/OUR-CLASSES.md`:

| action the agent can take | class | why |
|---|---|---|

Rules you must follow while doing this:

- **A/B/C never reach a human.** Internal/reversible, our own content into our own
  channels, and short on-topic replies to third parties are the agent's own decisions.
- **D = the human's hands are required** (2FA code, UAC prompt, password field, CAPTCHA).
  Not judgment — capability.
- **E = cannot be walked back by whoever notices it**: money, irreversible deletion,
  secrets leaving to a third party, legal commitments, mass-send.
- **Unsure between C and E → E.**
- Aim for a short D/E list. If more than a handful of this agent's routine actions land in
  D/E, say so out loud — that is a signal the agent has been given work it should not be
  doing unattended, and it is better to say it now than to build a queue.

Show me the table before moving on.

## Step 4 — wire it into the action path

Find where this agent decides to act (the tool dispatcher, the executor, the main loop).
Insert the gate so that **every** action passes through one of two calls:

```python
import json, subprocess, sys

GATE = ".approval-gate/approval_gate.py"

def _gate(args, stdin=None):
    return subprocess.run([sys.executable, GATE] + args, input=stdin,
                          capture_output=True, text=True, encoding="utf-8")

def authorized(action_text, klass):
    """A/B/C -> journal and proceed. D/E -> ask, then wait for the supervisor."""
    if klass in ("A", "B", "C"):
        _gate(["self", action_text, "--class", klass])
        return True
    r = _gate(["ask", action_text, "--class", klass])
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    q = json.loads(r.stdout)
    post_to_my_channel(q["ask_text"], q["targets"]["primary"])   # your transport
    return False   # NOT approved yet -- see Step 5
```

Three things you must get right, and I will check them:

- `authorized()` returning `False` means **not yet decided**. The agent must park the task
  and move on to other work — never busy-wait, never poll in a loop, never treat "asked"
  as "approved."
- The `action_text` must be **concrete and narrow**: `wire $4,800 to invoice #2211, vendor
  on file`, not `handle the vendor payment`. The human is approving that sentence and
  nothing wider.
- For class D, the text must contain the **click-path**: `open <app> > Settings > Devices,
  tap Approve, send me the 6-digit code`. "Approve the login" is not an instruction.

## Step 5 — the supervisor

`tick.py` collects replies and re-pings whatever went unanswered. Schedule it every 5
minutes — cron, systemd timer, or Windows Task Scheduler:

```bash
*/5 * * * * cd /path/to/.approval-gate && python tick.py --transport telegram
```

Run it **as its own process**, outside the agent. If the agent hangs, its questions must
still be re-pinged and eventually retired — a supervisor inside the thing it supervises
dies with it.

Pick the transport: `transports/telegram_bot.py` works out of the box (set
`APPROVAL_TG_TOKEN` from @BotFather). For Slack/Discord/email, read
`transports/README.md` and write the two functions.

## Step 6 — shadow first, then live

Before any of this touches a real person, run for a few days with
`--transport stdout`: every ask is printed instead of sent. Then:

```bash
python approval_gate.py metrics 7
```

If it shows more than a handful of asks per day, the class table from Step 3 is wrong —
go back and fix it before a human ever sees the channel. It is much easier to argue with
a log than with someone who has already muted you.

## Step 7 — prove it, do not tell me it works

Run these and show me the actual output:

1. `python approval_gate.py ask "<something class C>" --class C` → must exit 3, refused.
2. A full class E cycle: ask → reply `+` from the approver → `APPROVED` + `ACK`.
3. A stranger's `+` (any other sender_id) → must be ignored, and
   `grep reply_ignored_unknown_sender approval_log.jsonl` must show it.
4. A double answer → decided once, second reply ignored.
5. Silence: run `due` repeatedly past the window → `reping`, then `escalate`, then
   `stale`, then nothing forever.
6. `python tests/test_gate.py` → all green.

If any of these do not behave as described, that is a bug in the gate — open an issue at
https://github.com/tonydzi/agent-approval-gate/issues rather than working around it
locally.

## Finally

Write a short `docs/OUR-GATE.md` for whoever maintains this next: which channel, who can
approve, the class table, where the journal lives, and how to read
`python approval_gate.py metrics`. Then tell me the one thing about my setup you are least
comfortable with.
