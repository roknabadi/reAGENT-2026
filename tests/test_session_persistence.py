"""A conversation outlives the process that started it.

Sessions were held in a module-level dict with a 45-minute TTL. Three server
restarts in one afternoon — a config fix and two dependency fixes — destroyed
the reader's conversation each time, with no signal beyond the next follow-up
quietly running the whole Paperclip pull again. From the page that is
indistinguishable from statefulness that does not work, which is exactly what
it was reported as.

So a session now lasts until a new one is asked for: no time expiry, and the
record on disk. What does *not* survive is `runtime` — the live objects a
follow-up would otherwise reuse — and a restored session says so rather than
behaving as though it has them.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

import sessions as S  # noqa: E402


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _store(self, **kw):
        return S.Store(store_dir=self.dir, **kw)

    def test_a_session_survives_the_process_that_made_it(self):
        st = self._store()
        s = st.new()
        s.record = {"context": "Lung", "genes": ["E2F1"], "partner": "MED23"}
        s.questions = ["which TFs is lung cancer dependent on?"]
        st.save(s)

        after_restart = self._store()          # a new process, same disk
        got = after_restart.get(s.id)
        self.assertIsNotNone(got, "the id named nothing after a restart")
        self.assertEqual(got.record["context"], "Lung")
        self.assertEqual(got.questions, ["which TFs is lung cancer dependent on?"])

    def test_a_restored_session_admits_it_lost_its_live_objects(self):
        """`runtime` holds a docking box, an evidence dict and an agent trace.

        None of them are JSON. A follow-up that reuses them has to know they are
        gone; the effect is only ever to make a gate stricter, never to let one
        through, but it still has to be visible.
        """
        st = self._store()
        s = st.new(); s.record = {"context": "Lung"}; st.save(s)
        got = self._store().get(s.id)
        self.assertTrue(got.restored)
        self.assertEqual(got.runtime, {})

    def test_a_live_session_is_not_marked_restored(self):
        st = self._store()
        s = st.new(); s.record = {"context": "Lung"}; st.save(s)
        self.assertFalse(st.get(s.id).restored, "never left the process")

    def test_time_alone_does_not_end_a_session(self):
        """The default is no TTL. A demo left open over lunch still has its run."""
        clock = [1000.0]
        st = S.Store(store_dir=self.dir, clock=lambda: clock[0])
        s = st.new(); s.record = {"context": "Lung"}; st.save(s)
        clock[0] += 60 * 60 * 24 * 7          # a week
        self.assertIsNotNone(st.get(s.id))

    def test_an_explicit_ttl_still_expires(self):
        """Kept configurable: the tests and any caller that wants it can set one."""
        clock = [1000.0]
        st = S.Store(ttl=60, store_dir=None, clock=lambda: clock[0])
        s = st.new()
        clock[0] += 3600
        self.assertIsNone(st.get(s.id))

    def test_an_unknown_id_is_still_unknown(self):
        self.assertIsNone(self._store().get("no-such-session"))

    def test_a_truncated_record_is_ignored_rather_than_half_read(self):
        """A crash mid-write must not leave a session that parses as a short run."""
        st = self._store()
        s = st.new(); s.record = {"context": "Lung"}; st.save(s)
        Path(self.dir, f"{s.id}.json").write_text('{"id": "' + s.id + '", "rec')
        self.assertIsNone(self._store().get(s.id))

    def test_eviction_removes_the_file_too(self):
        """The cap bounds disk as well as memory, or it bounds nothing."""
        st = S.Store(limit=2, store_dir=self.dir)
        kept = []
        for _ in range(4):
            s = st.new(); s.record = {"n": 1}; st.save(s); kept.append(s.id)
        on_disk = {p.stem for p in Path(self.dir).glob("*.json")}
        self.assertLessEqual(len(on_disk), 2, f"{len(on_disk)} files for a cap of 2")
        self.assertIn(kept[-1], on_disk, "the most recent conversation was evicted")

    def test_persistence_can_be_switched_off(self):
        st = S.Store(store_dir=None)
        s = st.new(); s.record = {"context": "Lung"}; st.save(s)
        self.assertEqual(list(Path(self.dir).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
