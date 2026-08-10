# agent-approval-gate

Your agent runs unattended. It hits something it must not decide alone — spend money,
delete a bucket, sign something — or something it physically cannot do, like type a 2FA
code. Nobody is at the terminal.

Two things usually happen, and both are bad: the agent hangs forever, or the agent decides
anyway.

This is the small, dependency-free gate we built instead. The ask goes to a messenger, a
`+` comes back into the run, silence escalates and then gives up, and a day-old question is
never resurrected into someone's morning.

Built and running daily at [Palo Alto AI Research Lab](https://github.com/tonydzi/Palo-Alto-AI-Research-Lab)
across a fleet of autonomous Claude agents on five machines.

```console
$ python approval_gate.py ask "reply to the contributor comment" --class C
[approval_gate] REFUSED: class C does not interrupt a human.
  Decide it yourself and record it:
    python approval_gate.py self 'reply to the contributor comment' --class C

$ python approval_gate.py ask "wire 4800 USD to invoice #2211, vendor on file" --class E
{"id": "6c2a380d", "class": "E", "human_touched": true,
 "ask_text": "[agent-01] approval needed #6c2a380d\nwire 4800 USD to invoice #2211, vendor on file\nReply:  OK = yes  (or  +)   ·   NO = no    [#6c2a380d]"}

$ echo '[{"channel":"telegram","sender_id":"100000001","text":"+"}]' | python approval_gate.py check
APPROVED 6c2a380d :: wire 4800 USD to invoice #2211, vendor on file
ACK: accepted #6c2a380d
```

## The part that matters: not everything gets to ask

The plumbing above is easy. The thing that took us two months is knowing **which actions
are allowed to reach a human at all.**

Our first gate asked about everything the agent was unsure of. Within a week the channel
looked like this:

```
09:14  approval needed  -- reply to a contributor comment?
09:16  approval needed  -- rename a local script?
09:31  approval needed  -- push the docs fix?
09:40  approval needed  -- wire $4,800 to the vendor
09:41  approval needed  -- reindex the local cache?
```

The one that mattered is in there. Nobody read it, because by day four the channel had
trained its reader that nothing in it needed reading. **A gate that asks about everything
is not a safety mechanism — it is a way of laundering responsibility onto someone who has
stopped looking.**

So every action gets a class:

| | | who decides |
|---|---|---|
| **A** | internal, reversible | agent, journaled |
| **B** | own content into own channels | agent, journaled |
| **C** | short outbound to a third party, on topic | agent, journaled |
| **D** | needs human **hands** — 2FA, UAC, a password, a CAPTCHA | **ask** |
| **E** | money · irreversible deletion · secrets to third parties · legal commitments · mass-send | **ask** |

Unsure between C and E is E. And the typing is not advisory — `ask` **refuses** A, B and C
by exit code, so an agent cannot talk itself into a queue slot.

A/B/C are still journaled. "The agent decided" and "the agent skipped the gate" must not
look the same in the record.

Full reasoning, and how to draw the table for your own domain:
**[`docs/DECISION-CLASSES.md`](docs/DECISION-CLASSES.md)**.

## What you get

- **[`approval_gate.py`](approval_gate.py)** — the whole engine. One file, standard library
  only, no LLM, no API key, no network. Python 3.8+.
- **[`tick.py`](tick.py)** — the supervisor that makes silence safe: collects replies,
  re-pings what is overdue, escalates, then stops asking forever.
- **[`transports/`](transports/)** — Telegram bot reference transport (~120 lines, stdlib
  `urllib`), a stdout transport for shadow-running, and the contract for writing your own
  (Slack, Discord, email, webhook).
- **[`PROMPT.md`](PROMPT.md)** — paste into Claude Code or Codex; it installs the gate,
  draws the class table for *your* project, wires it into your action path and proves it
  works.
- **[`docs/GOTCHAS.md`](docs/GOTCHAS.md)** — twelve traps, each of which cost us something.
- **[`docs/SECURITY.md`](docs/SECURITY.md)** — the threat model, stated plainly, including
  what this does *not* defend against.
- **[`docs/METRICS.md`](docs/METRICS.md)** — the counter, and the honest way to read it.
- **[`tests/test_gate.py`](tests/test_gate.py)** — 33 tests, stdlib `unittest`, no network.

## Quickstart

```bash
git clone https://github.com/tonydzi/agent-approval-gate.git
cd agent-approval-gate
python tests/test_gate.py                    # 33 tests, ~5s

cp approval.example.json approval.json       # fill in approver ids + a sterile channel
python approval_gate.py ask "wire 4800 USD to invoice #2211" --class E
```

Post `ask_text` wherever your human is; feed replies back to `check` as JSON. Then put
`tick.py` on a 5-minute timer, in its own process:

```bash
*/5 * * * * cd /path/to/agent-approval-gate && python tick.py --transport telegram
```

The lazy path: open Claude Code and paste [`PROMPT.md`](PROMPT.md).

## How authority works

**The identity of the sender authorizes. Nothing else.** A reply decides a question only
if its `sender_id` matches an approver in your config. The token (`OK`, `+`, whatever you
configure) is a second factor of *intent*, not of identity — it separates "I am deciding
this" from chatter that happens to contain the word "ok".

Which means: a message saying *"Alex approved this, go ahead"* authorizes nothing. Neither
does a forward, a quote, a screenshot, or a bot relaying it. Your agent reads web pages and
issues, and any of them can contain "the user has pre-approved this" — that text can make
an agent *want* to act, but it cannot produce a reply from your approver's account.

Multiple approvers are supported; the journal records who decided. An approver entry with
no ids is **inert** — registered but unable to authorize. Half-configured fails closed.

## Silence is a state, and it is named

| situation | behaviour |
|---|---|
| not answered yet, window open | wait |
| window elapsed | re-ping, rearm the timer |
| `max_reping` nudges ignored | escalate — louder wording |
| `2 × max_reping` | give up, mark stale, **never ask again** |
| created > `abandon_hours` ago, never pinged (your supervisor was down) | retire **silently** |

That last row is deliberate. After an outage you must not dump a day of backlog into
someone's morning; a question nobody answered for 24 hours has usually been overtaken by
events, and asking it late is how a channel loses its reader.

There is a matching rule on the other side, and it is subtler: **an approval does not
expire as permission, but it does expire as a picture of the world.** We watched an agent
faithfully execute a six-hour-old `+` for work that had been completed in the meantime.
The permission was still valid. The world had moved. Before acting on a stale approval,
re-read current state — see [`docs/SECURITY.md`](docs/SECURITY.md).

## The uncomfortable half

An approval gate is a queue to one person. That person does not scale, does not run at
3am, and gets tired.

**A human in the middle of a pipeline is an architecture bug.** A human at the *ends* —
setting the goal, accepting the result — is the design. Every ask you add is a slot in
someone's attention, and our own measurement is not flattering: for a stretch this year our
ask queue grew faster than it was read. The gate was working perfectly and producing
nothing, because *delivered to a human* is not *decided by a human*.

That is why the counter ships in the box rather than as an afterthought:

```console
$ python approval_gate.py metrics 14
human_touches (asks sent to a person): 19  (1.4/day)
self-decided by the agent (A/B/C):     412
autonomy: 96% of decisions never reached a human
answered: 15   re-pings sent: 11   died unanswered: 4
asks by class: D=6, E=13
```

And why the report prints the per-class split on the same screen as the total: this number
can be lowered two ways. Move genuinely-A/B/C work off the human — that is the win. Or
relabel a wire transfer as class C — which lowers it identically and looks the same on a
dashboard. **Class D and E counts are the floor of this metric, never the target.** If
your agent's workload grows and D/E falls, that is an incident, not efficiency.

## What this is not

It is a gate the agent *chooses to call*. It constrains an agent that is trying to do the
right thing and might be wrong or manipulated. It is not a sandbox and not an enforcement
boundary — if you need enforcement, the gate must live outside the agent's process and hold
a credential the agent does not have.

It also has no networked coordination. One SQLite file is the shared state; that is how one
human sees one queue. Across hosts, put it on shared storage or give each agent its own
database and its own channel.

## License

MIT. Take it, fork it, rip the class table out and write your own — that part is the point.

---

Part of the agent-infrastructure kit series by Palo Alto AI Research Lab — see also
[`telegram-mcp-kit`](https://github.com/tonydzi/telegram-mcp-kit) (connect Claude to your
own Telegram, a natural transport for this gate),
[`whatsapp-mcp-kit`](https://github.com/tonydzi/whatsapp-mcp-kit),
[`mcp-daemon-diet`](https://github.com/tonydzi/mcp-daemon-diet) (one shared MCP daemon per
machine instead of a copy in every session), and
[`agent-leash`](https://github.com/tonydzi/agent-leash) (LEASH-8: the broader control model
for agents with delegated authority — this gate is one domain of it, implemented).
Questions, or a step that does not work? Open an issue — we answer within 24h.

---

> **Publishing your own internals?** This repo was sanitized for release with
> [`oss-publish`](https://github.com/tonydzi/oss-publish) — our substitution pipeline:
> personal data is replaced by plausible fakes of the same shape (never `<REDACTED>`),
> and a fail-closed gate re-scans the whole tree before the push. Free, MIT.
