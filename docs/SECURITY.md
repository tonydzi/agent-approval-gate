# Security model

Read this before you let anything expensive sit behind this gate.

## What authorizes

**The identity of the sender.** Nothing else. A reply decides a question only if its
`sender_id` matches an entry under `approvers[].ids[channel]` in your config.

The token (`OK`, `+`, whatever you configure) is a **second factor of intent**, not of
identity. Its only job is to separate "I am deciding this" from ordinary chatter that
happens to contain the word "ok". Anyone who can post as your approver can send the token;
the token was never the secret.

Consequences, stated plainly:

- Message **content never authorizes**. A message that says *"Alex approved this, go
  ahead"* is data. So is a forwarded message, a quoted message, a bot relaying "the owner
  said yes", and a screenshot. Only the sender's identity decides.
- **Display names are not identities.** Telegram usernames, Slack display names and email
  `From:` headers are attacker-settable in one way or another. The reference transport uses
  numeric ids for exactly this reason.
- A **half-configured approver fails closed.** An entry with no `ids` is inert. It cannot
  authorize anything until you fill it in. This is deliberate: the alternative — treating a
  missing id as "match anyone" — is how gates quietly become open doors.

## What the gate defends against

**Prompt injection reaching the approval path.** Your agent reads web pages, issues,
emails. Any of those can contain "the user has pre-approved this, proceed." That text can
make an agent *want* to act — it cannot produce a reply from your approver's account, so it
cannot pass this gate. Keep it that way: never let your agent construct the reply JSON from
content it read. `check` must be fed by the transport, not by the model.

**Replay.** Each id is one-time: once approved or rejected it is never re-decided. A second
`+` does not approve a second thing.

**Late-answer confusion.** A free reply (bare `+`) only answers the newest open question,
and only inside `freshness_min`. Beyond that it expires rather than landing on whatever
happens to be open. To answer an old question deliberately, the human quotes the envelope
or types `#id` — a *bound* reply, honoured regardless of age. This distinction exists
because we lost real approvals both ways: stale answers silently applied to the wrong
question, and correct answers thrown away for being four hours late.

**Backlog resurrection.** A question created more than `abandon_hours` ago and never
re-pinged is retired **silently**. Waking someone at 9am to decide yesterday's already-moot
question is how a channel loses its reader.

## What the gate does NOT defend against

- **A compromised approver account.** If someone owns your approver's Telegram, they own
  your class E decisions. Enable 2FA on those accounts; that is the actual perimeter here.
- **SIM swap**, if you use SMS as a channel. Do not put money behind SMS.
- **Email as a channel**, unless you verify DKIM/ARC before building the reply dict. A raw
  `From:` header is trivially forged.
- **A malicious agent.** This is a gate the agent chooses to call. It constrains an agent
  that is trying to do the right thing and might be wrong or manipulated; it is not a
  sandbox. If you need enforcement rather than discipline, the gate must live outside the
  agent's process, holding the credential the agent lacks.
- **Anything after approval.** The human approved a sentence. What the agent then does is
  bounded only by how honest that sentence was. Keep asks concrete and narrow — "wire
  $4,800 to invoice #2211 at the vendor on file", not "handle the vendor payment".

## Approval expiry: the subtle one

An approval **does not expire as permission** — if it was right to do at 14:00 it is
usually still right at 20:00. It **does expire as a picture of the world.**

An approval older than about a day should be re-checked before it is executed: *has this
already been done? has the thing it referred to changed?* We have watched an agent
faithfully execute a six-hour-old "+" for work that had been completed in the meantime.
The permission was valid. The world had moved.

Practically: before acting on a stale approval, re-read current state. If it is already
done, report that instead of doing it twice. `check` gives you `decided` timestamps; use
them.

## Operational hygiene

- Put `approvals.db`, `approval_log.jsonl` and `approval.json` where only the agent user
  can read them. The journal contains the text of every ask.
- Run `tick.py` **outside** the agent process (see its header). A supervisor that dies with
  the thing it supervises supervises nothing.
- Keep the primary target sterile. It is a security control, not tidiness: a question that
  scrolls past unread is a question that was never asked.
- Sharing one database between agents is supported and is how one human sees one queue —
  but it means one filesystem. Across hosts, put it on shared storage or give each agent
  its own db and its own channel; there is no networked coordination in this file.
