"""The primitives every other tool builds on: frozen header, gate-before-score, hash chain."""
import csv

from support import ROOT, WorkspaceTest, decision_row
from tools import fa_state as st


class TrackerSchemaTests(WorkspaceTest):
    def test_header_is_frozen_and_matches_the_shipped_template(self):
        expected = ('decision_id,created_on,origin,question,decision_type,amount_eur,instrument,'
                    'gate1,gate2,gate3,gate4,verdict,score,band,status,market_query_id,'
                    'ips_version,brief,notes')
        self.assertEqual(st.tracker_header(), expected)
        self.assertEqual(len(st.DECISION_FIELDS), 19)
        shipped = (ROOT / 'decision_tracker.template.csv').read_text(encoding='utf-8').strip()
        self.assertEqual(shipped, expected)

    def test_round_trip_preserves_every_field(self):
        path = self.work / 'decision_tracker.csv'
        st.write_tracker(path, [decision_row()])
        rows = st.read_tracker(path)
        self.assertEqual(rows, [decision_row()])

    def test_foreign_header_is_refused(self):
        path = self.work / 'decision_tracker.csv'
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow([*st.DECISION_FIELDS, 'extra_column'])
        with self.assertRaisesRegex(st.StateError, 'unexpected decision_tracker.csv header'):
            st.read_tracker(path)


class RowValidationTests(WorkspaceTest):
    def test_a_failed_gate_forces_gated_and_an_empty_score(self):
        with self.assertRaisesRegex(st.StateError, 'requires band gated'):
            st.validate_decision_row(decision_row(gate1='FAIL', score='80', band='do_now'))
        with self.assertRaisesRegex(st.StateError, 'must have an empty score'):
            st.validate_decision_row(decision_row(gate1='FAIL', score='80', band='gated'))
        st.validate_decision_row(decision_row(gate1='FAIL', score='', band='gated'))

    def test_gate4_never_fails_because_missing_evidence_is_undetermined(self):
        st.validate_decision_row(decision_row(gate4='UNDETERMINED', score='55', band='park'))
        with self.assertRaisesRegex(st.StateError, 'gate4 never FAILs'):
            st.validate_decision_row(decision_row(gate4='FAIL', score='', band='gated'))

    def test_vocabularies_are_closed(self):
        for field, value in (('origin', 'hunch'), ('decision_type', 'vibes'),
                             ('status', 'maybe'), ('band', 'yolo')):
            with self.subTest(field=field):
                with self.assertRaises(st.StateError):
                    st.validate_decision_row(decision_row(**{field: value}))

    def test_a_survivor_needs_a_numeric_score(self):
        with self.assertRaisesRegex(st.StateError, 'score'):
            st.validate_decision_row(decision_row(score=''))


class ChainTests(WorkspaceTest):
    def test_chain_links_and_breaks_on_tamper(self):
        path = self.work / 'actions.jsonl'
        events = []
        st.append_chain(path, events, 'action', {'what': 'bought a synthetic fund'})
        st.append_chain(path, events, 'action', {'what': 'sold a synthetic fund'})
        self.assertEqual(events[0]['previous'], st.GENESIS)
        self.assertEqual(events[1]['previous'], events[0]['hash'])
        self.assertEqual(len(st.read_chain(path)), 2)

        lines = path.read_text(encoding='utf-8').splitlines()
        lines[0] = lines[0].replace('bought', 'BOUGHT')
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(st.StateError, 'breaks the hash chain'):
            st.read_chain(path)

    def test_interrupted_write_is_reported_not_parsed(self):
        path = self.work / 'actions.jsonl'
        st.append_chain(path, [], 'action', {'what': 'synthetic'})
        with path.open('a', encoding='utf-8') as handle:
            handle.write('{"kind": "action"')
        with self.assertRaisesRegex(st.StateError, 'incomplete entry'):
            st.read_chain(path)


class AtomicWriteTests(WorkspaceTest):
    def test_write_replaces_without_leaving_temporaries(self):
        path = self.work / 'nested' / 'file.json'
        st.write_json(path, {'a': 1})
        st.write_json(path, {'a': 2})
        self.assertEqual(st.read_json(path, 'fixture'), {'a': 2})
        self.assertEqual([p.name for p in path.parent.iterdir()], ['file.json'])

    def test_lock_refuses_a_second_holder_and_names_the_first(self):
        lock = self.work / '.setup.lock'
        with st.exclusive_lock(lock):
            with self.assertRaisesRegex(st.StateError, 'another run holds'):
                with st.exclusive_lock(lock):
                    pass
        self.assertFalse(lock.exists())
