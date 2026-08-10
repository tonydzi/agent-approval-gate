#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""approval_gate.py -- human-in-the-loop approval for autonomous agents.

THE PROBLEM
  Your agent runs unattended. Sometimes it reaches something it must not decide alone
  (spend money, delete data, send a legal commitment) or physically cannot do (type a
  2FA code, click a UAC prompt). The human is not at the terminal. Two bad outcomes:
  the agent hangs forever, or the agent decides anyway.

WHAT THIS IS
  A small, dependency-free gate. It mints a question with an id, hands you an envelope
  to post into a messenger, matches the human's short reply back to that question, and
  makes silence a first-class outcome (re-ping, then escalate, then give up -- an old
  question is never resurrected into the human's face).

  Transport is YOUR problem and that is deliberate: this file never touches the network.
  It prints envelopes and reads replies as JSON. Telegram, Slack, Discord, email, SMS,
  a webhook -- see transports/.

THE PART THAT ACTUALLY MATTERS: DECISION CLASSES
  A gate that asks about everything is a gate nobody reads. Every action gets a class:

    A  internal / reversible ............. agent decides, journals it
    B  own content into own channels ..... agent decides, journals it
    C  short outbound to a third party ... agent decides, journals it
    D  needs human HANDS ................. ask (2FA code, UAC click, a password)
    E  serious ........................... ask (money, irreversible delete, secrets to
                                           third parties, legal commitments, mass-send)

  A/B/C never reach the human. `ask` REFUSES them by exit code -- the typing is enforced
  by the tool, not by good intentions. When unsure between C and E, it is E.

  See docs/DECISION-CLASSES.md for how we drew those lines, and docs/METRICS.md for the
  counter that tells you whether your gate is turning into a queue.

USAGE
  python approval_gate.py classify "<action>"                -> suggested class + why
  python approval_gate.py self     "<action>" --class A      -> journal, no human touched
  python approval_gate.py ask      "<action>" --class D      -> JSON {id, ask_text, targets}
  python approval_gate.py check  < replies.json              -> APPROVED/REJECTED/EXPIRED <id>
  python approval_gate.py due                                -> JSON re-ping envelopes
  python approval_gate.py pending                            -> open questions
  python approval_gate.py metrics [days]                     -> human_touches + class split
  python approval_gate.py gc                                 -> drop rows older than 7d

  Config:  ./approval.json  (or $APPROVAL_CONFIG)   -- start from approval.example.json
  State:   ./approvals.db   (or $APPROVAL_DB)       -- sqlite, one table
  Journal: ./approval_log.jsonl (or $APPROVAL_LOG)  -- append-only, survives gc

Python 3.8+. Standard library only. No LLM, no API key, no network. MIT.
"""

import os
import sys
import json
import re
import time
import sqlite3

try:  # make the tool safe to pipe on Windows consoles
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("APPROVAL_CONFIG", os.path.join(HERE, "approval.json"))
DB = os.environ.get("APPROVAL_DB", os.path.join(HERE, "approvals.db"))
# Append-only journal. Deliberately NOT the sqlite table: `gc` prunes the table, and the
# metrics below must still be able to answer "how many times did we interrupt a human last
# month?" long after those rows are gone.
LOG = os.environ.get("APPROVAL_LOG", os.path.join(HERE, "approval_log.jsonl"))

AGENT = os.environ.get("AGENT_NAME", os.environ.get("COMPUTERNAME", os.uname().nodename
                       if hasattr(os, "uname") else "agent")).strip()

SELF_CLASSES = ("A", "B", "C")   # agent decides these
ASK_CLASSES = ("D", "E")         # these may interrupt a human
CLASS_NAMES = {
    "A": "internal / reversible",
    "B": "own content into own channels",
    "C": "short outbound to a third party, on-topic",
    "D": "needs human hands (2FA / UAC / password / physical)",
    "E": "serious (money / irreversible delete / secrets to third parties / legal / mass-send)",
}


# --------------------------------------------------------------------------- plumbing

def _cfg():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.stderr.write(
            "[approval_gate] no config at %s\n"
            "  cp approval.example.json approval.json  and fill in your approver identities.\n"
            % CONFIG_PATH)
        sys.exit(2)
    except json.JSONDecodeError as e:
        sys.stderr.write("[approval_gate] config is not valid JSON: %s\n" % e)
        sys.exit(2)


def _now():
    # APPROVAL_NOW lets the test suite travel in time without sleeping.
    return int(os.environ.get("APPROVAL_NOW", int(time.time())))


def _gen_id():
    return os.urandom(4).hex()


def _conn():
    d = os.path.dirname(DB)
    if d:
        os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS pending(
        id TEXT PRIMARY KEY,
        text TEXT,
        agent TEXT,
        klass TEXT,
        created INTEGER,
        status TEXT,
        decided INTEGER,
        decided_by TEXT,
        reping INTEGER DEFAULT 0)""")
    return c


def _journal(event, **kw):
    """Never let bookkeeping break a decision."""
    try:
        rec = {"ts": _now(), "agent": AGENT, "event": event}
        rec.update(kw)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- classification

_HINTS = (
    ("E", ("wire", "transfer", "payment", "invoice", "refund", "purchase", "buy ",
           "usdt", "btc", "credit card", "$")),
    ("E", ("delete", "rm -rf", "drop table", "wipe", "erase", "purge", "empty trash",
           "force push", "revoke")),
    ("E", ("api key", "secret", "password", "credential", "token to", "private key",
           "share access")),
    ("E", ("contract", "sign", "agree to terms", "nda", "legal", "commit to pay")),
    ("E", ("mass", "bulk send", "everyone", "all users", "broadcast to", "mailing list")),
    ("D", ("2fa", "otp", "one-time code", "uac", "captcha", "passkey", "scan the qr",
           "physical", "plug in", "reboot the")),
    ("B", ("publish to our", "post to our", "our blog", "our channel", "changelog")),
    ("C", ("reply to", "email ", "dm ", "message ", "comment on", "answer the")),
    ("A", ("refactor", "rename", "run tests", "reindex", "cache", "local", "dry-run",
           "backup", "lint", "format")),
)


def suggest_class(text):
    """Heuristic first guess. Deliberately dumb and deliberately biased upward.

    This is a SUGGESTION, not a verdict: the caller passes --class explicitly. Its job is
    to catch the case where an agent talks itself into calling a wire transfer "routine".
    Order matters -- E patterns are checked first, and the tie-break rule from two months
    of running this is: if it could be C or it could be E, it is E.
    """
    t = (text or "").lower()
    for klass, words in _HINTS:
        for w in words:
            if w in t:
                return klass, "matched %r -> %s (%s)" % (w, klass, CLASS_NAMES[klass])
    return "E", "no confident match -> defaulting to E (unsure means ask)"


def cmd_classify(text):
    klass, why = suggest_class(text)
    route = "ASK a human" if klass in ASK_CLASSES else "agent decides, journal it"
    print(json.dumps({"suggested_class": klass, "name": CLASS_NAMES[klass],
                      "route": route, "why": why}, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- envelopes

def _envelope(cfg, mid, text, repeat=False):
    tok = cfg.get("token", "OK")
    head = ("REPEAT -- " if repeat else "") + "[%s] approval needed #%s" % (AGENT, mid)
    return ("%s\n%s\nReply:  %s = yes  (or  +)   ·   NO = no    [#%s]"
            % (head, text, tok, mid))


def _escalation(cfg, mid, text, reping, final=False):
    tok = cfg.get("token", "OK")
    tail = " -- last reminder, then I stop asking" if final else ""
    return ("[%s] STILL WAITING #%s (%d reminders, no answer)%s\n%s\n"
            "Reply:  %s = yes  (or  +)   ·   NO = no    [#%s]"
            % (AGENT, mid, reping, tail, text, tok, mid))


# --------------------------------------------------------------------------- self / ask

def cmd_self(text, klass):
    """Class A/B/C: the agent decides. This exists so that 'I decided it myself' leaves a
    record. An agent with authority and no journal is indistinguishable from an agent that
    quietly skipped the gate."""
    if klass not in SELF_CLASSES:
        sys.stderr.write("[approval_gate] class %s must go through `ask`, not `self`\n" % klass)
        sys.exit(2)
    mid = _gen_id()
    _journal("self_decided", id=mid, klass=klass, text=(text or "")[:300])
    print(json.dumps({"id": mid, "class": klass, "decision": "self",
                      "human_touched": False}, ensure_ascii=False))


def cmd_ask(text, klass):
    """Class D/E only: mint a question and hand back an envelope to post.

    Refusing A/B/C here is the whole point. Every ask is a slot in one human's queue; a
    gate that accepts everything degrades into a channel nobody reads, and then the one
    question that mattered scrolls past with the rest.
    """
    cfg = _cfg()
    if klass in SELF_CLASSES:
        guess, why = suggest_class(text)
        sys.stderr.write(
            "[approval_gate] REFUSED: class %s (%s) does not interrupt a human.\n"
            "  Decide it yourself and record it:\n"
            "    python approval_gate.py self %r --class %s\n"
            "  (classifier on this text says: %s)\n" % (klass, CLASS_NAMES[klass], text, klass, why))
        sys.exit(3)
    if klass not in ASK_CLASSES:
        sys.stderr.write("[approval_gate] unknown class %r (use A B C D E)\n" % klass)
        sys.exit(2)
    if klass == "D" and not _looks_actionable(text):
        sys.stderr.write(
            "[approval_gate] WARNING: class D means you need the human's HANDS. Say exactly\n"
            "  what to click or type -- 'approve the login' is not an instruction, \n"
            "  'open <app> > Settings > Devices, tap Approve, send me the 6-digit code' is.\n")
    created = _now()
    forced = os.environ.get("APPROVAL_FORCE_ID")
    c = _conn()
    try:
        # Plain INSERT, never INSERT OR REPLACE. Ids are 32 bits of urandom; across many
        # agents sharing one db a collision is unlikely but not impossible, and REPLACE
        # would silently overwrite somebody else's open question with ours -- their human
        # would then be answering a question that no longer exists. Collide -> new id.
        for attempt in range(8):
            mid = forced or _gen_id()
            try:
                c.execute("INSERT INTO pending(id,text,agent,klass,created,status,decided,decided_by,reping) "
                          "VALUES (?,?,?,?,?,?,?,?,0)",
                          (mid, text, AGENT, klass, created, "pending", None, None))
                c.commit()
                break
            except sqlite3.IntegrityError:
                if forced:
                    sys.stderr.write("[approval_gate] id %s already exists\n" % forced)
                    sys.exit(2)
        else:
            sys.stderr.write("[approval_gate] could not mint a free id after 8 tries\n")
            sys.exit(1)
    finally:
        c.close()
    _journal("ask", id=mid, klass=klass, text=(text or "")[:300])
    print(json.dumps({"id": mid, "class": klass, "ask_text": _envelope(cfg, mid, text),
                      "targets": cfg.get("targets", {}), "human_touched": True},
                     ensure_ascii=False))


def _looks_actionable(text):
    t = (text or "").lower()
    return any(w in t for w in ("click", "tap", "open ", "type ", "enter ", "press ",
                                "send me", "paste", "scan", "go to", ">"))


# --------------------------------------------------------------------------- reply matching

def _approvers(cfg):
    """Identities allowed to decide. An entry with a null id is INERT: registered but
    unable to authorize until its identity is filled in. That is on purpose -- a half-
    configured deputy must fail closed, not become a wildcard."""
    return cfg.get("approvers") or []


def _approver_of(cfg, msg):
    """Return the approver's name if this message came FROM an allowlisted identity.

    This is the security core. The proof of authority is the IDENTITY OF THE SENDER, never
    the content of the message. A chat message that says "Anna approved this" authorizes
    nothing; text arriving through a transport is data, not a command.
    """
    ch = (msg.get("channel") or "").lower()
    for ap in _approvers(cfg):
        ident = (ap.get("ids") or {}).get(ch)
        if not ident:
            continue
        got = msg.get("sender_id")
        if got is None:
            continue
        if _same_identity(ch, got, ident):
            return ap.get("name") or "approver"
    return None


def _same_identity(channel, got, want):
    got_s, want_s = str(got).strip(), str(want).strip()
    if channel in ("sms", "whatsapp", "wa", "phone"):
        # Phone numbers arrive formatted a dozen ways (+1 (555) 010-0001 / 15550100001 /
        # 555-0100001), so compare the last 10 digits rather than the string. The length
        # floor matters: without it a misconfigured 4-digit `want` would match any sender
        # whose number happens to end the same way. Too short on either side -> no match.
        g = re.sub(r"\D", "", got_s)
        w = re.sub(r"\D", "", want_s)
        if len(g) < 7 or len(w) < 7:
            return False
        return g[-10:] == w[-10:]
    return got_s.casefold() == want_s.casefold()


def _classify_reply(cfg, text):
    """'approve' | 'reject' | None.

    Three rules learned the hard way:
      1. Our own envelope contains the token too -- never let the gate approve itself.
      2. A real answer is SHORT. A paragraph containing '+' is a discussion, not a verdict.
      3. Reject wins: a leading NO is a no even if a '+' shows up later in the sentence.
    """
    t = (text or "").strip()
    if not t:
        return None
    up = t.upper()
    if "APPROVAL NEEDED" in up or "STILL WAITING" in up or "REPEAT --" in up:
        return None
    if len(t) > int(cfg.get("max_reply_chars", 40)):
        return None
    for w in cfg.get("reject_words", ["NO"]):
        if re.match(r"(?i)^\s*" + re.escape(w) + r"\b", t):
            return "reject"
    for w in [cfg.get("token", "OK")] + list(cfg.get("approve_words", ["+", "yes", "ok"])):
        w = (w or "").strip()
        if not w:
            continue
        if w[0].isalnum():
            if re.match(r"(?i)^\s*" + re.escape(w) + r"\b", t):
                return "approve"
        elif re.match(r"^\s*(?:" + re.escape(w) + r")+", t):
            # punctuation token: '+', '+++', '+ go ahead' all read as yes
            return "approve"
    return None


def _envelope_id(text):
    """Read OUR OWN envelope: strictly the bracketed `[#a1b2]` marker at its end.

    Deliberately stricter than _typed_id, because the two jobs differ. The ask text is
    written by the agent and routinely contains other hashes -- `wire 4800 to invoice
    #2211` is a perfectly normal question, and `#2211` is four valid hex digits. A loose
    search would bind the human's answer to the invoice number instead of the question.
    Only the bracketed form is ours.
    """
    m = re.search(r"\[#([0-9a-fA-F]{4,8})\]", text or "")
    return m.group(1).lower() if m else None


def _typed_id(text):
    """Read what the HUMAN typed: `#a1b2` anywhere in a short reply. The `#` is mandatory
    -- without it "yes 4090" would bind to a question id that does not exist, and chat is
    full of hex-looking words. Prefer a bracketed marker if they pasted one."""
    bracketed = _envelope_id(text)
    if bracketed:
        return bracketed
    m = re.search(r"#([0-9a-fA-F]{4,8})\b", text or "")
    return m.group(1).lower() if m else None


def cmd_check():
    """Read candidate replies from stdin, decide what (if anything) they answered.

    stdin = JSON list of {"channel","sender_id","text","reply_to_text"(optional)}.
    """
    cfg = _cfg()
    raw = sys.stdin.read()
    try:
        msgs = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as e:
        sys.stderr.write("[approval_gate] bad JSON on stdin: %s\n" % e)
        sys.exit(1)
    if isinstance(msgs, dict):
        msgs = msgs.get("messages", [msgs])
    fresh = int(cfg.get("freshness_min", 15)) * 60
    c = _conn()
    decided_any = False
    try:
        for m in msgs:
            if not isinstance(m, dict):
                continue
            verdict = _classify_reply(cfg, m.get("text"))
            if not verdict:
                continue
            approver = _approver_of(cfg, m)
            if not approver:
                _journal("reply_ignored_unknown_sender", channel=m.get("channel"))
                continue

            # BOUND vs FREE. Bound = the human quoted our envelope, or typed #id. We then
            # know exactly which question they mean, so we honour it even past the window
            # (real incident: an answer arrived by reply four hours later and was thrown
            # away as stale). Free = a bare "+": it answers the newest open question only.
            bound = _envelope_id(m.get("reply_to_text")) or _typed_id(m.get("text"))
            if bound:
                row = c.execute("SELECT id,text,created,status FROM pending WHERE id=?",
                                (bound,)).fetchone()
                if not row:
                    continue  # someone else's id -- do not touch our own questions
            else:
                row = c.execute("SELECT id,text,created,status FROM pending "
                                "WHERE status='pending' ORDER BY created DESC LIMIT 1").fetchone()
            if not row:
                continue
            pid, ptext, created, status = row

            # One-time: an already-decided question is never re-decided. A second "+" is
            # not a second approval.
            if status in ("approved", "rejected"):
                continue
            if status != "pending" and not bound:
                continue
            if not bound and _now() - created > fresh:
                # The answer is ambiguous (a bare '+' long after the fact -- which question
                # did they mean?), so we do NOT honour it. But the question stays PENDING.
                #
                # An earlier version set status='expired' here, and `due` only looks at
                # pending rows: the question was silently retired the moment someone tried
                # to answer it late. The agent then waited forever for a decision that had
                # been thrown away -- the exact hang this whole file exists to prevent.
                # Now the supervisor re-asks it on the next tick, with a fresh envelope the
                # human can reply to unambiguously.
                _journal("late_free_reply_ignored", id=pid, age_s=_now() - created)
                print("EXPIRED %s :: %s  (bare reply older than %sm is ambiguous -- "
                      "not honoured; the question stays open and will be re-asked)"
                      % (pid, ptext, cfg.get("freshness_min", 15)))
                decided_any = True
                continue

            new_status = "approved" if verdict == "approve" else "rejected"
            by = "%s@%s:%s" % (approver, m.get("channel"), m.get("sender_id"))
            # The status guard is repeated in the UPDATE on purpose. The SELECT above and
            # this write are two statements: with two supervisors running (or a retry that
            # overlaps its predecessor) both can read 'pending' and both can write, and the
            # second silently overturns the first. Deciding inside the write, and believing
            # only a rowcount of 1, is what actually makes a question one-time.
            n = c.execute("UPDATE pending SET status=?, decided=?, decided_by=? "
                          "WHERE id=? AND status NOT IN ('approved','rejected')",
                          (new_status, _now(), by, pid)).rowcount
            c.commit()
            if not n:
                continue  # somebody else decided it between our read and our write
            decided_any = True
            _journal(new_status, id=pid, by=by, bound=bool(bound))
            print("%s %s :: %s" % (new_status.upper(), pid, ptext))
            print("ACK: %s #%s" % ("accepted" if new_status == "approved" else "rejected", pid))
    finally:
        c.close()
    if not decided_any:
        print("(no valid decision from an allowlisted approver in this batch)")


# --------------------------------------------------------------------------- supervisor

def cmd_pending():
    c = _conn()
    try:
        rows = c.execute("SELECT id,text,klass,created,status,decided_by "
                         "FROM pending ORDER BY created DESC LIMIT 30").fetchall()
    finally:
        c.close()
    if not rows:
        print("(no questions)")
        return
    now = _now()
    for pid, text, klass, created, status, by in rows:
        extra = " by %s" % by if by else ""
        print("#%s  [%s/%s%s]  %dm ago  :: %s"
              % (pid, klass or "?", status, extra, (now - created) // 60, text))


def cmd_due():
    """Silence handling. For each question still pending past its window:
      - re-ping (same envelope, marked REPEAT), rearming the timer;
      - after `max_reping` nudges, get louder (escalation envelope);
      - after 2x that, mark it stale and stop asking forever.
    A question created long ago and never re-pinged (the process was down) is retired
    SILENTLY: waking someone up about yesterday's decision is worse than not asking.

    Prints JSON; the caller posts each envelope. See tick.py.
    """
    cfg = _cfg()
    fresh = int(cfg.get("freshness_min", 15)) * 60
    cap = int(cfg.get("max_reping", 4))
    abandon = int(cfg.get("abandon_hours", 24)) * 3600
    out = []
    c = _conn()
    try:
        rows = c.execute("SELECT id,text,created,reping FROM pending WHERE status='pending'").fetchall()
        for pid, text, created, reping in rows:
            age = _now() - created
            if age <= fresh:
                continue
            reping = reping or 0
            if reping == 0 and age > abandon:
                c.execute("UPDATE pending SET status='stale' WHERE id=?", (pid,))
                c.commit()
                _journal("abandoned_silently", id=pid, age_h=age // 3600)
                continue
            if reping >= cap * 2:
                c.execute("UPDATE pending SET status='stale', reping=? WHERE id=?", (reping + 1, pid))
                c.commit()
                _journal("gave_up", id=pid, reping=reping + 1)
                out.append({"kind": "stale", "id": pid, "text": text,
                            "envelope": _escalation(cfg, pid, text, reping, final=True)})
                continue
            c.execute("UPDATE pending SET created=?, reping=? WHERE id=?", (_now(), reping + 1, pid))
            c.commit()
            louder = (reping + 1) >= cap
            env = (_escalation(cfg, pid, text, reping + 1) if louder
                   else _envelope(cfg, pid, text, repeat=True))
            _journal("reping", id=pid, n=reping + 1, louder=louder)
            out.append({"kind": "escalate" if louder else "reping",
                        "id": pid, "text": text, "envelope": env})
    finally:
        c.close()
    print(json.dumps({"due": out, "targets": cfg.get("targets", {})}, ensure_ascii=False))


# --------------------------------------------------------------------------- metrics

def cmd_metrics(days=14):
    """human_touches -- the number the gate exists to drive DOWN.

    A gate is a queue to one person. Shipping it is easy; the failure mode is that the
    queue grows faster than the human drains it, and then the gate is decoration. Watch
    two things: touches/day, and how many of them were class C-shaped noise that should
    have been decided by the agent.

    READ THIS BEFORE OPTIMISING THE NUMBER. Class D and E touches are the FLOOR, not the
    waste. The only honest way to lower this metric is to move genuinely-A/B/C work off
    the human; reclassifying a wire transfer as class C also lowers it, and that is the
    exact failure this whole file exists to prevent. The breakdown below is printed per
    class for that reason: a D/E count that falls while your agent does more is not a win,
    it is an incident. See docs/METRICS.md.
    """
    import datetime as _dt
    rows = []
    try:
        with open(LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    except FileNotFoundError:
        print("(no approval_log.jsonl yet -- nothing has gone through the gate)")
        return
    cutoff = _now() - days * 86400
    rows = [r for r in rows if r.get("ts", 0) >= cutoff]
    if not rows:
        print("(no gate activity in the last %dd)" % days)
        return

    asks = [r for r in rows if r.get("event") == "ask"]
    selfs = [r for r in rows if r.get("event") == "self_decided"]
    answered = [r for r in rows if r.get("event") in ("approved", "rejected")]
    stale = [r for r in rows if r.get("event") in ("gave_up", "abandoned_silently")]
    repings = [r for r in rows if r.get("event") == "reping"]

    total = len(asks) + len(selfs)
    per_day = {}
    for r in asks:
        d = _dt.datetime.fromtimestamp(r.get("ts", 0)).strftime("%Y-%m-%d")
        per_day[d] = per_day.get(d, 0) + 1
    by_class = {}
    for r in asks:
        by_class[r.get("klass", "?")] = by_class.get(r.get("klass", "?"), 0) + 1

    budget = int(_cfg().get("touches_per_day_budget", 6)) if os.path.exists(CONFIG_PATH) else 6
    print("window: last %dd" % days)
    print("human_touches (asks sent to a person): %d  (%.1f/day)" % (len(asks), len(asks) / max(1, days)))
    print("self-decided by the agent (A/B/C):     %d" % len(selfs))
    if total:
        print("autonomy: %.0f%% of decisions never reached a human" % (100.0 * len(selfs) / total))
    print("answered: %d   re-pings sent: %d   died unanswered: %d" % (len(answered), len(repings), len(stale)))
    if asks:
        print("asks by class: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(by_class.items())))
        print("  (D and E are the FLOOR of this metric, not waste -- driving them to zero "
              "means you reclassified something you should not have)")
    noisy = [d for d, n in per_day.items() if n > budget]
    print("")
    for d in sorted(per_day)[-days:]:
        print("  %s: %d%s" % (d, per_day[d], "   OVER BUDGET" if per_day[d] > budget else ""))
    print("")
    if noisy:
        print("%d day(s) over the budget of %d asks/day. Read them: how many were really D or E?"
              % (len(noisy), budget))
    else:
        print("within budget (<= %d asks/day). The human is not being used as a queue." % budget)
    if stale and answered:
        ratio = 100.0 * len(stale) / (len(stale) + len(answered))
        if ratio > 20:
            print("WARNING: %.0f%% of questions died unanswered -- either the channel is not "
                  "sterile, or you are asking about things nobody cares to decide." % ratio)


def cmd_gc():
    c = _conn()
    try:
        n = c.execute("DELETE FROM pending WHERE created < ?", (_now() - 7 * 86400,)).rowcount
        c.commit()
        print("removed %d row(s) (the journal at %s is untouched)" % (n, os.path.basename(LOG)))
    finally:
        c.close()


# --------------------------------------------------------------------------- cli

USAGE = __doc__.split("USAGE", 1)[1].strip() if "USAGE" in (__doc__ or "") else "see --help"


def _pop_class(argv):
    for flag in ("--class", "-c"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                k = argv[i + 1].strip().upper()
                return k, argv[:i] + argv[i + 2:]
            return None, argv[:i]
    return None, argv


def main(argv):
    cmd = argv[0] if argv else "pending"
    rest = argv[1:]
    klass, rest = _pop_class(rest)
    text = " ".join(rest).strip()

    if cmd == "classify" and text:
        cmd_classify(text)
    elif cmd == "self" and text:
        if not klass:
            sys.stderr.write("[approval_gate] `self` needs --class A|B|C\n")
            sys.exit(2)
        cmd_self(text, klass)
    elif cmd == "ask" and text:
        if text.startswith("-"):
            sys.stderr.write('usage: approval_gate.py ask "<action>" --class D|E\n')
            sys.exit(2)
        if not klass:
            guess, why = suggest_class(text)
            sys.stderr.write("[approval_gate] `ask` needs --class D|E (classifier suggests %s: %s)\n"
                             % (guess, why))
            sys.exit(2)
        cmd_ask(text, klass)
    elif cmd == "check":
        cmd_check()
    elif cmd == "due":
        cmd_due()
    elif cmd == "pending":
        cmd_pending()
    elif cmd == "metrics":
        cmd_metrics(int(rest[0]) if rest and rest[0].isdigit() else 14)
    elif cmd == "gc":
        cmd_gc()
    else:
        print(USAGE)


if __name__ == "__main__":
    main(sys.argv[1:])
