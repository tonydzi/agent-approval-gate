# Gotchas

Every one of these cost us something real. In rough order of how much.

### 1. The gate approved itself

Our ask envelope contains the token, because it tells the human what to reply with. The
reply-reader then found the token in our own message and read it as an approval. The fix
in `_classify_reply` is two lines — skip anything containing the envelope's own markers,
and ignore any message longer than `max_reply_chars`.

The general form: **a channel where you both write and read is a loop.** Anything you emit
must be recognisable as yours before you parse it as input.

### 2. Asks into a working channel are invisible within a day

The first version posted questions into the team's main channel. They were buried under
status messages, heartbeats and conversation in minutes. Nobody was ignoring them — they
genuinely could not be seen.

A sterile channel that contains **nothing but questions** made answers start arriving the
same day. This is why `targets.primary` exists and why the docs nag about it. It is a
functional requirement, not a preference.

### 3. Answers arriving four hours later were thrown away

We had a freshness window so an old "+" could not be replayed onto a new question. Correct.
But the human's habit is to *reply* to the notification when they finally see it — and a
reply is unambiguous about which question it means. We were discarding perfectly clear
answers as stale.

Hence bound vs free:

- **bound** (quoted envelope, or `#id` typed by hand) → honoured at any age;
- **free** (bare `+`) → newest open question, inside the window only.

### 4. `#` is mandatory in a bound reply

The first version of the id-matcher accepted any hex-looking word. Chat is full of them.
"yes 4090" bound an approval to a nonexistent question id and silently decided nothing;
worse shapes would have bound it to the *wrong* question. The regex now requires the `#`.

### 5. `ask --help` created a real question

`approval_gate.py ask --help` registered a pending question whose text was `--help`, and
the supervisor duly re-pinged a human about it, twice. Anything that mints state from
`argv` needs to reject flags in the text position.

### 6. `INSERT OR REPLACE` can eat someone else's question

Ids are 32 bits of urandom. With one agent that never collides in practice; with a fleet
sharing one database it eventually will, and `INSERT OR REPLACE` silently overwrites the
other agent's open question — whose human then answers a question that no longer exists.
Plain `INSERT`, catch `IntegrityError`, mint a new id.

### 7. The supervisor died with the thing it supervised

An early version ran the re-ping loop inside the agent. When the agent hung, its questions
stopped being re-pinged — the exact moment the mechanism was most needed. `tick.py` is a
separate process on a separate timer for that reason, and it is why `due` retires ancient
never-pinged questions silently: after an outage you must not dump a day of backlog into
someone's morning.

### 8. Silence is a state, and you must name it

Three different silences need three different behaviours:

| Situation | Behaviour |
|---|---|
| Not answered yet, window open | wait |
| Window elapsed | re-ping, rearm timer |
| `max_reping` nudges ignored | escalate (louder wording) |
| `2 × max_reping` | give up, mark stale, **never ask again** |
| Created > `abandon_hours` ago, never pinged | retire **silently** |

Collapsing these into "retry until answered" is how a gate turns into a nag, and a nag
gets muted.

### 9. Deputies with empty ids

We added a deputy to the allowlist before we had their user id, leaving the field null.
An early version compared `None == None` for a message with no sender and matched. Inert
entries must fail closed; there is now an explicit `if not ident: continue`.

### 10. Timestamps in tests

Testing a 24-hour window with `time.sleep` is not testing. `APPROVAL_NOW` overrides the
clock so the suite travels in time; `tests/test_gate.py` uses it for every window case.
If you fork the engine, keep that seam.

### 11. Phone numbers never match exactly

`+1 (555) 010-0001`, `15550100001`, `555-0100001` — same human, three strings. The engine
compares the last 10 digits for phone-shaped channels. Exact string comparison here will
silently reject your own approver.

### 12. "Delivered" is not "decided"

The one that matters most and has no code fix. A question posted to a channel is not a
question answered. Our ask queue once grew faster than it was read, and the gate reported
itself perfectly healthy the whole time — it *had* delivered everything. Watch
`died unanswered` in [`METRICS.md`](METRICS.md), not delivery success.
