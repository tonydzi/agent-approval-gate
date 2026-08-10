# Metrics: `human_touches`, and the honest way to read it

```console
$ python approval_gate.py metrics 14
window: last 14d
human_touches (asks sent to a person): 19  (1.4/day)
self-decided by the agent (A/B/C):     412
autonomy: 96% of decisions never reached a human
answered: 15   re-pings sent: 11   died unanswered: 4
asks by class: D=6, E=13
  (D and E are the FLOOR of this metric, not waste -- driving them to zero
   means you reclassified something you should not have)
```

## The uncomfortable half

An approval gate is a queue to one person. That person does not scale, does not run at
3am, and gets tired. **A human in the middle of a pipeline is an architecture bug**; a
human at the *ends* — setting the goal, accepting the result — is the design.

So the gate is a tool with a cost, and the cost is measurable: every ask is a slot in
someone's attention. Our own measurement, honestly: for a stretch this year the ask queue
grew faster than it was read. The gate was working perfectly and producing nothing,
because "delivered to a human" is not "decided by a human."

That is why the counter ships with the gate rather than as an afterthought. If you cannot
say how many times you interrupted a person last month, you cannot tell a safety mechanism
from a habit.

## What to actually watch

**`autonomy` %** — should climb as your class table gets better. If it is falling, either
your agent is doing genuinely riskier work (fine) or your classification is drifting
upward out of nervousness (not fine).

**`died unanswered`** — the sharpest signal in the report. Questions that got asked,
re-pinged, and then went stale. Above ~20% means one of three things, all worth fixing:

- your channel is not sterile and the asks are buried;
- you are asking about things nobody cares to decide (they were class C all along);
- the questions are unanswerable as written — especially class D without a click-path.

**`re-pings sent`** vs `answered` — a high ratio means your `freshness_min` is shorter than
your human's actual response time. Lengthen the window rather than nagging harder.

**per-day counts against `touches_per_day_budget`** — a mirror, not a limiter. The engine
will never refuse a legitimate class E ask because you exceeded a budget. The budget exists
so you notice the day the gate became chatty, and go read those asks.

## The trap: do not optimize this number directly

`human_touches` can be lowered two ways.

1. Move genuinely-A/B/C work off the human. This is the win.
2. Relabel a wire transfer as class C. This also lowers the number.

Route 2 is available, cheap, and looks identical on the dashboard — which is exactly why
the per-class breakdown is printed on the same screen as the total. **Class D and E counts
are the floor of this metric.** If your agent's workload grows and your D/E count falls,
that is not efficiency; it is an incident, and the journal will show you which asks stopped
being asked.

If you set an OKR on this number, set it on the *autonomy percentage* and put a floor
under D/E, never a target on total touches.

## Reading the journal directly

`approval_log.jsonl` is append-only and survives `gc`. One JSON object per line:

```json
{"ts": 1754870000, "agent": "agent-01", "event": "ask", "id": "a1b2", "klass": "E", "text": "wire 4800 ..."}
{"ts": 1754870400, "agent": "agent-01", "event": "reping", "id": "a1b2", "n": 1, "louder": false}
{"ts": 1754871000, "agent": "agent-01", "event": "approved", "id": "a1b2", "by": "Alex Rivera@telegram:100000001", "bound": true}
```

Events: `ask`, `self_decided`, `approved`, `rejected`, `expired`, `reping`,
`gave_up`, `abandoned_silently`, `reply_ignored_unknown_sender`.

That last one is your intrusion signal. A steady trickle of replies from senders who are
not on the allowlist means somebody — or something — is trying to answer for your approver.
