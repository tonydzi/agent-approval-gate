#!/usr/bin/env python3
"""Two supervisors decide the SAME question at once: one must win, the other must not overturn it.

The window between the SELECT and the UPDATE in `cmd_check` is microseconds, so a plain
"run two processes" test passes by luck on broken code. This one forces the interleave: each
process pauses just before its UPDATE until the other has also read 'pending' (file barrier).
Supervisor A answers "+ #id", supervisor B answers "NO #id".

    python tests/test_race.py

To see it fail, point GATE_UNDER_TEST at a copy of approval_gate.py whose UPDATE has lost
its `AND status NOT IN ('approved','rejected')` guard.
"""
import os, sys, json, shutil, tempfile, subprocess, sqlite3, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(os.path.dirname(HERE), "approval_gate.py")
sys.path.insert(0, HERE)
from test_gate import CONFIG  # same config as the main suite

LAUNCH = r'''
import sys, os, time, sqlite3, runpy
me, other = os.environ["BARRIER_ME"], os.environ["BARRIER_OTHER"]
real = sqlite3.connect
class Proxy:
    def __init__(self, c): self._c = c
    def __getattr__(self, n): return getattr(self._c, n)
    def execute(self, sql, *a):
        if sql.lstrip().upper().startswith("UPDATE PENDING SET STATUS"):
            open(me, "w").close()                      # I have read 'pending' and am about to write
            t = time.time()
            while not os.path.exists(other) and time.time() - t < 8: time.sleep(0.01)
        return self._c.execute(sql, *a)
sqlite3.connect = lambda *a, **k: Proxy(real(*a, **k))
sys.argv = [os.environ["GATE"], "check"]
runpy.run_path(os.environ["GATE"], run_name="__main__")
'''


class TestRace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate-race-")
        cfg = os.path.join(self.tmp, "approval.json")
        with open(cfg, "w") as f:
            json.dump(CONFIG, f)
        self.env = dict(os.environ, APPROVAL_CONFIG=cfg, GATE=os.environ.get("GATE_UNDER_TEST", GATE),
                        APPROVAL_DB=os.path.join(self.tmp, "a.db"),
                        APPROVAL_LOG=os.path.join(self.tmp, "log.jsonl"),
                        AGENT_NAME="race", PYTHONIOENCODING="utf-8", APPROVAL_NOW="1000000")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_supervisors_one_decision(self):
        gate = self.env["GATE"]
        q = json.loads(subprocess.run([sys.executable, gate, "ask", "wire 4800 to the vendor", "--class", "E"],
                                      env=self.env, capture_output=True, text=True).stdout)
        qid = q["id"]
        fa, fb = os.path.join(self.tmp, "a.ready"), os.path.join(self.tmp, "b.ready")

        def spawn(me, other, text):
            env = dict(self.env, BARRIER_ME=me, BARRIER_OTHER=other)
            msg = json.dumps([{"channel": "telegram", "sender_id": "100000001",
                               "text": text, "reply_to_text": ""}])
            p = subprocess.Popen([sys.executable, "-c", LAUNCH], env=env, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            p.stdin.write(msg)
            p.stdin.close()
            return p

        pa, pb = spawn(fa, fb, "+ #%s" % qid), spawn(fb, fa, "NO #%s" % qid)
        oa, ob = pa.stdout.read(), pb.stdout.read()
        pa.wait()
        pb.wait()
        final = sqlite3.connect(self.env["APPROVAL_DB"]).execute(
            "SELECT status FROM pending WHERE id=?", (qid,)).fetchone()[0]
        acks = [o for o in (oa, ob) if "ACK:" in o]
        with open(self.env["APPROVAL_LOG"]) as f:
            events = [json.loads(l)["event"] for l in f if l.strip()]
        decided = [e for e in events if e in ("approved", "rejected")]
        self.assertTrue(os.path.exists(fa) and os.path.exists(fb), "barrier never formed: race not forced")
        self.assertEqual(len(acks), 1, "exactly one supervisor may ACK; got %r / %r" % (oa, ob))
        self.assertEqual(len(decided), 1, "exactly one decision journaled; got %r" % decided)
        self.assertEqual(final, decided[0], "stored status must equal the one journaled decision")


if __name__ == "__main__":
    unittest.main()
