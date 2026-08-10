#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test suite for approval_gate.py. Stdlib unittest, no network, no fixtures on disk.

    python tests/test_gate.py            # or: python -m unittest discover tests

Every test drives the CLI as a subprocess, the same way your agent will. Each one runs in
a fresh temp dir with its own config, db and journal, and time travel is done with
APPROVAL_NOW rather than sleeping.

If you change the engine and these still pass, check that they can fail: comment out the
`if not approver: continue` line in `_approver_of` and `test_stranger_cannot_approve`
must go red. A suite that cannot fail on broken code proves nothing.
"""

import os
import sys
import json
import shutil
import tempfile
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(os.path.dirname(HERE), "approval_gate.py")

CONFIG = {
    "token": "OK",
    "approve_words": ["+", "yes", "ok"],
    "reject_words": ["NO", "STOP"],
    "max_reply_chars": 40,
    "freshness_min": 15,
    "max_reping": 2,
    "abandon_hours": 24,
    "touches_per_day_budget": 6,
    "approvers": [
        {"name": "Alex Rivera", "ids": {"telegram": "100000001", "sms": "+1 (555) 010-0001"}},
        {"name": "Unfilled Deputy", "ids": {}},
    ],
    "targets": {"primary": "-1000000000001"},
}


class GateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate-test-")
        self.cfg = os.path.join(self.tmp, "approval.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f)
        self.env = dict(os.environ)
        self.env.update({
            "APPROVAL_CONFIG": self.cfg,
            "APPROVAL_DB": os.path.join(self.tmp, "approvals.db"),
            "APPROVAL_LOG": os.path.join(self.tmp, "approval_log.jsonl"),
            "AGENT_NAME": "test-agent",
            "PYTHONIOENCODING": "utf-8",
        })

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_gate(self, args, stdin=None, now=None):
        env = dict(self.env)
        if now is not None:
            env["APPROVAL_NOW"] = str(now)
        return subprocess.run([sys.executable, GATE] + args, input=stdin,
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def ask(self, text="wire 4800 to the vendor", klass="E", now=1000000):
        r = self.run_gate(["ask", text, "--class", klass], now=now)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def reply(self, text, sender="100000001", channel="telegram", reply_to=""):
        return json.dumps([{"channel": channel, "sender_id": sender,
                            "text": text, "reply_to_text": reply_to}])


# --------------------------------------------------------------- classification

class TestClasses(GateCase):
    def test_ask_refuses_self_classes(self):
        for k in ("A", "B", "C"):
            r = self.run_gate(["ask", "reply to the issue comment", "--class", k])
            self.assertEqual(r.returncode, 3, "class %s should be refused" % k)
            self.assertIn("REFUSED", r.stderr)

    def test_self_refuses_ask_classes(self):
        for k in ("D", "E"):
            r = self.run_gate(["self", "wire money", "--class", k])
            self.assertEqual(r.returncode, 2)

    def test_self_is_journaled_without_touching_a_human(self):
        r = self.run_gate(["self", "reindex the local cache", "--class", "A"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(json.loads(r.stdout)["human_touched"])
        with open(self.env["APPROVAL_LOG"], encoding="utf-8") as f:
            events = [json.loads(l)["event"] for l in f if l.strip()]
        self.assertEqual(events, ["self_decided"])

    def test_classifier_defaults_to_E_when_unsure(self):
        r = self.run_gate(["classify", "zorble the frobnicator"])
        self.assertEqual(json.loads(r.stdout)["suggested_class"], "E")

    def test_classifier_catches_money(self):
        r = self.run_gate(["classify", "wire 4800 to the vendor"])
        self.assertEqual(json.loads(r.stdout)["suggested_class"], "E")

    def test_ask_without_class_is_refused(self):
        r = self.run_gate(["ask", "delete the production bucket"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("suggests E", r.stderr)

    def test_flag_in_text_position_does_not_create_a_question(self):
        # gotcha #5: `ask --help` used to register a real question named "--help"
        r = self.run_gate(["ask", "--help"])
        self.assertNotEqual(r.returncode, 0)
        p = self.run_gate(["pending"])
        self.assertIn("(no questions)", p.stdout)


# --------------------------------------------------------------- happy path

class TestDecide(GateCase):
    def test_approve(self):
        q = self.ask()
        r = self.run_gate(["check"], stdin=self.reply("+"), now=1000060)
        self.assertIn("APPROVED %s" % q["id"], r.stdout)
        self.assertIn("ACK: accepted #%s" % q["id"], r.stdout)

    def test_reject(self):
        q = self.ask()
        r = self.run_gate(["check"], stdin=self.reply("NO"), now=1000060)
        self.assertIn("REJECTED %s" % q["id"], r.stdout)

    def test_reject_wins_over_a_later_plus(self):
        self.ask()
        r = self.run_gate(["check"], stdin=self.reply("NO + not this one"), now=1000060)
        self.assertIn("REJECTED", r.stdout)

    def test_token_works_as_well_as_plus(self):
        q = self.ask()
        r = self.run_gate(["check"], stdin=self.reply("OK"), now=1000060)
        self.assertIn("APPROVED %s" % q["id"], r.stdout)

    def test_phone_identity_matches_on_last_ten_digits(self):
        q = self.ask()
        r = self.run_gate(["check"], now=1000060,
                          stdin=self.reply("+", sender="15550100001", channel="sms"))
        self.assertIn("APPROVED %s" % q["id"], r.stdout)


# --------------------------------------------------------------- breakers

class TestBreakers(GateCase):
    def test_stranger_cannot_approve(self):
        self.ask()
        r = self.run_gate(["check"], stdin=self.reply("+", sender="999999999"), now=1000060)
        self.assertIn("no valid decision", r.stdout)
        self.assertIn("[pending]", self.run_gate(["pending"]).stdout.replace("E/pending", "pending"))

    def test_inert_approver_cannot_approve(self):
        # the deputy with no ids must never match, even on a message with no sender
        self.ask()
        r = self.run_gate(["check"], now=1000060,
                          stdin=json.dumps([{"channel": "telegram", "sender_id": None, "text": "+"}]))
        self.assertIn("no valid decision", r.stdout)

    def test_double_answer_decides_once(self):
        q = self.ask()
        first = self.run_gate(["check"], stdin=self.reply("+"), now=1000060)
        self.assertIn("APPROVED", first.stdout)
        second = self.run_gate(["check"], stdin=self.reply("NO"), now=1000120)
        self.assertIn("no valid decision", second.stdout)
        self.assertIn("approved", self.run_gate(["pending"]).stdout)
        self.assertNotIn("rejected", self.run_gate(["pending"]).stdout)

    def test_double_answer_bound_to_the_same_id_decides_once(self):
        # the free-reply case above is caught by the "not pending" guard; only a BOUND
        # second answer actually exercises one-time-ness. Found by sabotaging the guard
        # and watching the suite stay green.
        q = self.ask()
        self.run_gate(["check"], stdin=self.reply("+", reply_to=q["ask_text"]), now=1000060)
        second = self.run_gate(["check"], now=1000120,
                               stdin=self.reply("NO", reply_to=q["ask_text"]))
        self.assertIn("no valid decision", second.stdout)
        self.assertIn("approved", self.run_gate(["pending"]).stdout)
        self.assertNotIn("rejected", self.run_gate(["pending"]).stdout)

    def test_our_own_envelope_cannot_approve_itself(self):
        q = self.ask()
        r = self.run_gate(["check"], stdin=self.reply(q["ask_text"]), now=1000060)
        self.assertIn("no valid decision", r.stdout)

    def test_long_message_is_discussion_not_a_verdict(self):
        self.ask()
        chat = "+ well I think we should probably do this but let me check with finance first"
        r = self.run_gate(["check"], stdin=self.reply(chat), now=1000060)
        self.assertIn("no valid decision", r.stdout)

    def test_free_reply_expires_past_the_window(self):
        q = self.ask(now=1000000)
        r = self.run_gate(["check"], stdin=self.reply("+"), now=1000000 + 16 * 60)
        self.assertIn("EXPIRED %s" % q["id"], r.stdout)

    def test_bound_reply_is_honoured_long_after_the_window(self):
        q = self.ask(now=1000000)
        four_hours = 1000000 + 4 * 3600
        r = self.run_gate(["check"], now=four_hours,
                          stdin=self.reply("+", reply_to=q["ask_text"]))
        self.assertIn("APPROVED %s" % q["id"], r.stdout)

    def test_hex_word_without_hash_does_not_bind(self):
        # gotcha #4: "yes 4090" must not bind to a question id
        q = self.ask(now=1000000)
        r = self.run_gate(["check"], stdin=self.reply("yes 4090"), now=1000060)
        self.assertIn("APPROVED %s" % q["id"], r.stdout)  # free reply -> newest question

    def test_hash_inside_the_question_text_does_not_hijack_the_binding(self):
        # a real question contains real hashes: "wire 4800 to invoice #2211". "2211" is
        # four valid hex digits, and a loose matcher binds the answer to the invoice
        # number instead of the question. Only the bracketed [#id] marker is ours.
        q = self.ask("wire 4800 USD to invoice #2211, vendor on file", "E", now=1000000)
        r = self.run_gate(["check"], now=1000000 + 4 * 3600,
                          stdin=self.reply("+", reply_to=q["ask_text"]))
        self.assertIn("APPROVED %s" % q["id"], r.stdout)

    def test_unknown_bound_id_touches_nothing(self):
        self.ask()
        r = self.run_gate(["check"], stdin=self.reply("+ #dead"), now=1000060)
        self.assertIn("no valid decision", r.stdout)
        self.assertIn("pending", self.run_gate(["pending"]).stdout)

    def test_empty_and_garbage_stdin(self):
        self.assertIn("no valid decision", self.run_gate(["check"], stdin="").stdout)
        self.assertIn("no valid decision", self.run_gate(["check"], stdin="[]").stdout)
        bad = self.run_gate(["check"], stdin="{not json")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("bad JSON", bad.stderr)

    def test_missing_config_fails_closed_with_instructions(self):
        env = dict(self.env)
        env["APPROVAL_CONFIG"] = os.path.join(self.tmp, "nope.json")
        r = subprocess.run([sys.executable, GATE, "ask", "x", "--class", "E"],
                           capture_output=True, text=True, env=env, encoding="utf-8")
        self.assertEqual(r.returncode, 2)
        self.assertIn("approval.example.json", r.stderr)


# --------------------------------------------------------------- silence

class TestSilence(GateCase):
    def test_not_due_inside_the_window(self):
        self.ask(now=1000000)
        due = json.loads(self.run_gate(["due"], now=1000000 + 60).stdout)["due"]
        self.assertEqual(due, [])

    def test_reping_then_escalate_then_stale(self):
        self.ask(now=1000000)
        kinds, t = [], 1000000
        for _ in range(6):
            t += 16 * 60
            due = json.loads(self.run_gate(["due"], now=t).stdout)["due"]
            if due:
                kinds.append(due[0]["kind"])
        self.assertEqual(kinds[0], "reping")
        self.assertIn("escalate", kinds)
        self.assertEqual(kinds[-1], "stale")
        self.assertIn("stale", self.run_gate(["pending"]).stdout)

    def test_stale_question_is_never_asked_again(self):
        self.ask(now=1000000)
        t = 1000000
        for _ in range(8):
            t += 16 * 60
            self.run_gate(["due"], now=t)
        t += 16 * 60
        self.assertEqual(json.loads(self.run_gate(["due"], now=t).stdout)["due"], [])

    def test_ancient_backlog_is_retired_silently(self):
        self.ask(now=1000000)
        due = json.loads(self.run_gate(["due"], now=1000000 + 25 * 3600).stdout)["due"]
        self.assertEqual(due, [], "a day-old never-pinged question must not be resurrected")
        self.assertIn("stale", self.run_gate(["pending"]).stdout)

    def test_repinged_question_still_accepts_a_bound_answer(self):
        q = self.ask(now=1000000)
        self.run_gate(["due"], now=1000000 + 16 * 60)
        r = self.run_gate(["check"], now=1000000 + 40 * 60,
                          stdin=self.reply("+", reply_to=q["ask_text"]))
        self.assertIn("APPROVED %s" % q["id"], r.stdout)


# --------------------------------------------------------------- metrics

class TestMetrics(GateCase):
    def test_counts_touches_and_autonomy(self):
        for i in range(3):
            self.run_gate(["self", "local task %d" % i, "--class", "A"], now=1000000)
        self.ask(now=1000000)
        out = self.run_gate(["metrics", "14"], now=1000000).stdout
        self.assertIn("human_touches (asks sent to a person): 1", out)
        self.assertIn("self-decided by the agent (A/B/C):     3", out)
        self.assertIn("autonomy: 75%", out)

    def test_reports_the_floor_warning(self):
        self.ask(now=1000000)
        out = self.run_gate(["metrics"], now=1000000).stdout
        self.assertIn("FLOOR", out)

    def test_gc_keeps_the_journal(self):
        self.ask(now=1000000)
        r = self.run_gate(["gc"], now=1000000 + 8 * 86400)
        self.assertIn("removed 1", r.stdout)
        self.assertTrue(os.path.getsize(self.env["APPROVAL_LOG"]) > 0)
        self.assertIn("human_touches (asks sent to a person): 1",
                      self.run_gate(["metrics"], now=1000000 + 8 * 86400).stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
