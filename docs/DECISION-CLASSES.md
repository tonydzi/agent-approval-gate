# Decision classes

This is the part of the kit that took two months to learn. Everything else is plumbing.

## The mistake everyone makes first

You give an agent autonomy, you get nervous, you add an approval gate, and you route
*everything the agent is not sure about* through it. Within a week the channel looks like
this:

```
09:14  approval needed #7a21  -- reply to a contributor comment?
09:16  approval needed #7a22  -- rename a local script?
09:31  approval needed #7a23  -- push the docs fix?
09:40  approval needed #7a24  -- wire $4,800 to the vendor
09:41  approval needed #7a25  -- reindex the local cache?
```

The one that mattered is in there. Nobody read it, because by day four the channel had
trained its reader that nothing in it needs reading. **A gate that asks about everything
is not a safety mechanism; it is a way of laundering responsibility onto someone who has
stopped looking.**

The fix is not "ask less." It is to decide, in advance and in writing, which *kinds* of
action can reach a human at all.

## The five classes

| Class | What it is | Who decides |
|---|---|---|
| **A** | Internal, reversible. Local files, refactors, tests, caches, indexes, dry runs. | Agent. Journal it. |
| **B** | Your own content into your own channels. Your blog, your changelog, your repo. | Agent. Journal it. |
| **C** | Short outbound to a third party, on topic. Answering a question someone asked you, replying in a thread you are already in. | Agent. Journal it. |
| **D** | Needs human **hands**. A 2FA code, a UAC prompt, a password field, a physical device, a CAPTCHA. | Ask. |
| **E** | Serious. Money. Irreversible deletion. Secrets going to a third party. Legal or contractual commitments. Mass-send. A new peer joining a trusted system. | Ask. |

Two rules on top:

1. **Unsure between C and E is E.** Not "use your judgment" — the tie-break is written
   down, so it does not get re-litigated at 2am by a tired agent optimizing for progress.
2. **A/B/C are still journaled.** "The agent decided" and "the agent skipped the gate"
   must not look the same in the record. `approval_gate.py self "<action>" --class A`
   costs nothing and is the difference between delegated authority and no authority at all.

## Why the split falls where it does

The boundary is not *risk*, it is *reversibility crossed with who can undo it*.

- A bad class A action costs a `git revert`.
- A bad class B post costs an edit and mild embarrassment. You own the channel.
- A bad class C reply costs an apology. Someone else read it, but it is a sentence, and
  the same human would have written roughly the same sentence.
- Class D is not about risk at all. The agent is not deciding anything — it *cannot act*.
  The human is a peripheral here, not an approver.
- Class E is where a mistake cannot be walked back by the person who made it. Money that
  left, data that is gone, a secret that another party now has, a signature.

That is also why class D questions have a different *shape*. An E question asks
**should I?**; a D question asks **would you press this?** — so a D question that does not
contain a click-path is a broken question. `ask --class D` will warn you about it:

```
BAD :  approve the login
GOOD:  open <app> > Settings > Devices > pending, tap Approve,
       then send me the 6-digit code
```

## Enforcing it

The typing is not advisory. `ask` refuses A, B and C:

```console
$ python approval_gate.py ask "reply to the issue comment" --class C
[approval_gate] REFUSED: class C (short outbound to a third party, on-topic) does not interrupt a human.
  Decide it yourself and record it:
    python approval_gate.py self 'reply to the issue comment' --class C
```

`classify` gives a first guess and defaults to **E** when it does not recognise anything —
biased upward on purpose, because the expensive error is a wire transfer that got typed as
routine, not a refactor that got typed as serious.

```console
$ python approval_gate.py classify "wire 4800 to the vendor"
{ "suggested_class": "E", "route": "ASK a human", "why": "matched 'wire' -> E ..." }
```

The classifier is a seatbelt, not a driver. Your agent passes `--class` explicitly, and
the honest way to run this is to write your own class table for your own domain — the five
above are ours, and the *method* is what transfers, not our exact word lists.

## Adapting it

Sit down once with whoever carries the consequences and answer, for your system:

1. What can this agent do that cannot be undone by the person who notices it?  → **E**
2. What can it not physically do alone?  → **D**
3. Everything else → A, B or C, and write down the journal line.

Then run [`docs/METRICS.md`](METRICS.md) for two weeks and let the counts tell you where
you drew a line in the wrong place. Ours moved twice.
