# agent-approval-gate

Your agent runs unattended. It hits something it must not decide alone — spend money,
delete a bucket, sign something — or something it physically cannot do, like type a 2FA
code. Nobody is at the terminal.

Two things usually happen, and both are bad: the agent hangs forever, or the agent decides
anyway.

This is the small, dependency-free gate we built instead: [approval_gate.py](approval_gate.py). The ask goes to a messenger, a
`+` comes back into the run, silence escalates and then gives up, and a day-old question is
never resurrected into someone's morning.

Built and running daily at [Palo Alto AI Research Lab](https://github.com/tonydzi/tonydzi)
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

Unsure between C and E is E. And the typing is not advisory — [approval_gate.py](approval_gate.py) **refuses** A, B and C by exit code, so an agent cannot talk itself into a queue slot.

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
- **[`docs/GOTCHAS.md`](docs/GOTCHAS.md)** — thirteen traps, each of which cost us something.
- **[`docs/SECURITY.md`](docs/SECURITY.md)** — the threat model, stated plainly, including
  what this does *not* defend against.
- **[`docs/METRICS.md`](docs/METRICS.md)** — the counter, and the honest way to read it.
- **[`tests/test_gate.py`](tests/test_gate.py)** — 36 tests, stdlib `unittest`, no network.

## Quickstart

```bash
git clone https://github.com/tonydzi/agent-approval-gate.git
cd agent-approval-gate
python tests/test_gate.py                    # 36 tests, ~5s

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
if its `sender_id` matches an approver in your config. The token (`OK`, `+`, whatever you configure) is a second factor of *intent*, not of identity — it separates "I am deciding this" from chatter that happens to contain the word "ok", and [docs/SECURITY.md](docs/SECURITY.md) draws that line in full.

Which means: a message saying *"Alex approved this, go ahead"* authorizes nothing. Neither
does a forward, a quote, a screenshot, or a bot relaying it. Your agent reads web pages and
issues, and any of them can contain "the user has pre-approved this" — that text can make
an agent *want* to act, but it cannot produce a reply from your approver's account.

Multiple approvers are supported; the journal written by [approval_gate.py](approval_gate.py) records who decided. An approver entry with
no ids is **inert** — registered but unable to authorize. Half-configured fails closed.

## Silence is a state, and it is named

| situation | behaviour |
|---|---|
| not answered yet, window open | wait |
| window elapsed | re-ping, rearm the timer |
| `max_reping` nudges ignored | escalate — louder wording |
| `2 × max_reping` | give up, mark stale, **never ask again** |
| created > `abandon_hours` ago, never pinged (your supervisor was down) | retire **silently** |

That last row is deliberate. After an outage [tick.py](tick.py) must not dump a day of backlog into someone's morning; a question nobody answered for 24 hours has usually been overtaken by events, and asking it late is how a channel loses its reader.

There is a matching rule on the other side, and it is subtler: **an approval does not
expire as permission, but it does expire as a picture of the world.** We watched an agent
faithfully execute a six-hour-old `+` for work that had been completed in the meantime.
The permission was still valid. The world had moved. Before acting on a stale approval,
re-read current state — see [`docs/SECURITY.md`](docs/SECURITY.md).

## The uncomfortable half

An approval gate is a queue to one person. That person does not scale, does not run at
3am, and gets tired.

**A human in the middle of a pipeline is an architecture bug.** A human at the *ends* —
setting the goal, accepting the result — is the design. Every ask you add is a slot in someone's attention, and our own measurement in [docs/METRICS.md](docs/METRICS.md) is not flattering: for a stretch of 2026 our ask queue grew faster than it was read. The gate was working perfectly and producing
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
dashboard. **Class D and E counts are the floor of this metric, never the target**, as [docs/METRICS.md](docs/METRICS.md) spells out. If your agent's workload grows and D/E falls, that is an incident, not efficiency.

## What this is not

It is a gate the agent *chooses to call*. It constrains an agent that is trying to do the
right thing and might be wrong or manipulated. It is not a sandbox and not an enforcement boundary — if you need enforcement, the gate must live outside the agent's process and hold a credential the agent does not have, a limit stated plainly in [docs/SECURITY.md](docs/SECURITY.md).

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
Rolling that approved change out to more than one machine? [`fleet-deploy`](https://github.com/tonydzi/fleet-deploy) does it with a canary order and a verify that must read the fact back, so "applied" is not a synonym for "sent".
Questions, or a step that does not work? Open an issue — we answer within 24h.

---

> **Publishing your own internals?** This repo was sanitized for release with
> [`oss-publish`](https://github.com/tonydzi/oss-publish) — our substitution pipeline:
> personal data is replaced by plausible fakes of the same shape (never `<REDACTED>`),
> and a fail-closed gate re-scans the whole tree before the push. Free, MIT.

---

<!--kits-series:start-->

## 🧰 Connector & Ops Kits

Eight kits, all published 2026-08-10, each lifted out of the same live fleet after it
survived production rather than written as a demo. They are independent: take one, ignore
the rest. All stdlib-only Python, all free.

| kit | what it solves |
|---|---|
| [`telegram-mcp-kit`](https://github.com/tonydzi/telegram-mcp-kit) | Connect your agent to your own Telegram account in ~15 minutes, with the production patches and every gotcha |
| [`whatsapp-mcp-kit`](https://github.com/tonydzi/whatsapp-mcp-kit) | Link WhatsApp, using a live self-refreshing QR page that makes pairing actually work |
| [`mcp-daemon-diet`](https://github.com/tonydzi/mcp-daemon-diet) | One shared MCP daemon per machine instead of a stdio copy in every session, with a watchdog that will not blind your live sessions |
| [`agent-approval-gate`](https://github.com/tonydzi/agent-approval-gate) | Your agent needs a human's OK and nobody is at the terminal: the ask goes to a messenger, the answer comes back into the run |
| [`fleet-deploy`](https://github.com/tonydzi/fleet-deploy) | Roll a fix to N machines and prove it landed on each one: canary waves and a verify that must read a fact back |
| [`secondop-panel`](https://github.com/tonydzi/secondop-panel) | Nobody reviews themselves, and one reviewer model is one blind spot: fan a change out to several model families with quorum and honest skips |
| [`oss-publish`](https://github.com/tonydzi/oss-publish) | Open up internal work without leaking it: plausible substitutions of the same shape, then a fail-closed gate over the whole tree |
| [`llm-spend-audit`](https://github.com/tonydzi/llm-spend-audit) | What your own wiring charges on every session, and which paid subscriptions are going undrawn |

<!--kits-series:end-->

<!--ecosystem-map:start-->

## 🧩 One piece of a working system

This repository is one piece lifted out of a live operation: one engineer running operations,
an AI cofounder, and a fleet of machines that reach consensus with each other and wake the
human only for money or the irreversible. It was extracted after it survived production,
not written as a demo — and it runs on its own: nothing here phones home to the rest.

**See how the whole thing fits together → [SYSTEM.md](https://github.com/tonydzi/tonydzi/blob/main/SYSTEM.md)**

**Want your machine in the fleet? → [Join the fleet](https://github.com/tonydzi/join-the-fleet)** (15 minutes, one link, no account with us)

Its closest neighbours in the **governance** layer: [`claude-bible`](https://github.com/tonydzi/claude-bible) · [`agent-leash`](https://github.com/tonydzi/agent-leash) · [`charm-os`](https://github.com/tonydzi/charm-os)

<!--ecosystem-map:end-->

## AI contributors

This project is built by a human + AI team, and the git log says so under the rules in [AI-CONTRIBUTORS.md](https://github.com/tonydzi/.github/blob/main/AI-CONTRIBUTORS.md): Claude writes most of the code, Codex and Grok review it, Gemini feeds the research. Each is credited on a commit
**only if its output changed that commit's content** — no decorative credits. Lab-wide
policy, one source for every repo: [AI-CONTRIBUTORS.md](https://github.com/tonydzi/.github/blob/main/AI-CONTRIBUTORS.md).

<!-- READ-WITH-AI:START (generated by read_with_ai.py - do not hand-edit) -->

### READ THIS WITH AI

One click and an agent reads the repo, pulls out the patterns and helps you apply them to your own work.

<a href="https://chatgpt.com/codex?prompt=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fagent-approval-gate%20%28%E2%80%9Cagent-approval-gate%E2%80%9D%20-%20Your%20agent%20needs%20a%20human%27s%20OK%20and%20nobody%20is%20at%20the%20terminal.%20Ask%20goes%20to%20a%20messenger%2C%20%27%2B%27%20comes%20back%20into%20the%20run%2C%20silence%20escalates%20then%20gives%20up%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="Codex - open" src="https://img.shields.io/badge/Codex-open-000000?style=for-the-badge&logo=openai&logoColor=white"></a> <a href="https://chatgpt.com/?q=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fagent-approval-gate%20%28%E2%80%9Cagent-approval-gate%E2%80%9D%20-%20Your%20agent%20needs%20a%20human%27s%20OK%20and%20nobody%20is%20at%20the%20terminal.%20Ask%20goes%20to%20a%20messenger%2C%20%27%2B%27%20comes%20back%20into%20the%20run%2C%20silence%20escalates%20then%20gives%20up%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="ChatGPT - open" src="https://img.shields.io/badge/ChatGPT-open-10a37f?style=for-the-badge&logo=openai&logoColor=white"></a> <a href="https://claude.ai/new?q=Read%20this%20repo%3A%20https%3A%2F%2Fgithub.com%2Ftonydzi%2Fagent-approval-gate%20%28%E2%80%9Cagent-approval-gate%E2%80%9D%20-%20Your%20agent%20needs%20a%20human%27s%20OK%20and%20nobody%20is%20at%20the%20terminal.%20Ask%20goes%20to%20a%20messenger%2C%20%27%2B%27%20comes%20back%20into%20the%20run%2C%20silence%20escalates%20then%20gives%20up%29.%20Work%20out%20what%20problem%20it%20actually%20solves%2C%20pull%20out%20the%20reusable%20patterns%20and%20help%20me%20apply%20them%20to%20my%20own%20setup.%20Start%20by%20asking%20what%20I%20am%20working%20on."><img alt="Claude - open" src="https://img.shields.io/badge/Claude-open-d97757?style=for-the-badge&logo=anthropic&logoColor=white"></a>

<details>
<summary>Copy the prompt (works in any agent: Gemini, Grok, a local model, your own CLI)</summary>

```text
Read this repo: https://github.com/tonydzi/agent-approval-gate (“agent-approval-gate” - Your agent needs a human's OK and nobody is at the terminal. Ask goes to a messenger, '+' comes back into the run, silence escalates then gives up). Work out what problem it actually solves, pull out the reusable patterns and help me apply them to my own setup. Start by asking what I am working on.
```

</details>

<sub>— TonyDzi, Palo Alto AI Research Lab · second brain, agent coordination, persistent memory: github.com/tonydzi</sub>

<!-- READ-WITH-AI:END -->
