import copy
import tempfile
import unittest
from pathlib import Path
from rescue_mission.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'attempt.sqlite3'
        self.s = Store(self.path)
        self.s.seed([{'id': 'find', 'target': 'survivor', 'room': 'kitchen',
                      'relation': 'right'},
                     {'id': 'all', 'target': 'survivor', 'mode': 'collection'}])
        self.event = {
            'event_id': 'e1', 'type': 'observation', 'source': 'validator',
            'observed_at': '2026-11-17T12:00:00+08:00',
            'data': {'id': 'view1', 'entity_id': 'person1', 'label': 'survivor',
                     'room': 'kitchen', 'relation': 'right', 'relation_reference': 'entrance',
                     'confidence': .95, 'verified': True, 'evidence_ref': 'image1',
                     'location': {'frame_id': 'map', 'x': 1, 'y': 2, 'z': 0}}}

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def test_persistence_and_no_reseed_reset(self):
        self.s.apply(self.event)
        self.s.close()
        self.s = Store(self.path)
        self.s.seed([{'id': 'find', 'target': 'survivor'}])
        self.assertEqual(self.s.get('objective', 'find')['status'], 'complete')
        self.assertEqual(self.s.get('objective', 'all')['status'], 'active')

    def test_unverified_or_invalid_location_cannot_complete(self):
        for change in ({'verified': False}, {'location': None},
                       {'location': {'frame_id': 'camera', 'x': 1, 'y': 2, 'z': 0}},
                       {'confidence': .5}):
            with self.subTest(change=change):
                event = copy.deepcopy(self.event)
                event['event_id'] = str(change)
                event['data']['id'] = str(change)
                event['data'].update(change)
                self.s.apply(event)
                self.assertEqual(self.s.get('objective', 'find')['status'], 'pending')

    def test_missing_relation_reference(self):
        del self.event['data']['relation_reference']
        self.s.apply(self.event)
        self.assertEqual(self.s.get('objective', 'find')['status'], 'pending')

    def test_idempotency_and_entity_deduplication(self):
        self.assertTrue(self.s.apply(self.event))
        self.assertFalse(self.s.apply(self.event))
        self.event['event_id'] = 'e2'
        self.event['data']['id'] = 'view2'
        self.s.apply(self.event)
        self.assertEqual(self.s.get('objective', 'all')['findings'], ['person1'])

    def test_bad_event_rolls_back(self):
        self.event['data']['confidence'] = float('nan')
        with self.assertRaises(ValueError):
            self.s.apply(self.event)
        self.assertEqual(self.s.rows('observation'), [])
        self.event['data']['confidence'] = .95
        self.assertTrue(self.s.apply(self.event))

    def test_action_success_does_not_complete_search(self):
        req = self.s.issue('r1')
        self.assertEqual(self.s.issue('r2')['id'], 'r1')
        self.s.apply(dict(event_id='result1', type='action_result', source='nav',
                          observed_at=self.event['observed_at'],
                          data={'id': req['id'], 'status': 'succeeded'}))
        self.assertEqual(self.s.get('objective', 'find')['status'], 'pending')

    def test_collection_requires_explicit_coverage(self):
        self.s.apply(self.event)
        self.s.apply(dict(event_id='coverage', type='search_complete', source='coverage_validator',
                          observed_at=self.event['observed_at'],
                          data={'id': 'all', 'evidence_ref': 'coverage-report.json'}))
        self.assertEqual(self.s.get('objective', 'all')['status'], 'complete')

    def test_failed_request_blocks_until_explicit_recovery(self):
        req = self.s.issue('r1')
        self.s.apply(dict(event_id='failure', type='action_result', source='nav',
                          observed_at=self.event['observed_at'],
                          data={'id': req['id'], 'status': 'failed', 'detail': 'unreachable'}))
        self.assertEqual(self.s.get('objective', req['objective_id'])['status'], 'blocked')
        self.s.apply(dict(event_id='retry', type='objective_status', source='recovery',
                          observed_at=self.event['observed_at'],
                          data={'id': req['objective_id'], 'status': 'pending'}))
        self.assertEqual(self.s.get('objective', req['objective_id'])['status'], 'pending')

    def test_invalid_input_and_conflicting_retransmission(self):
        self.s.apply(self.event)
        self.event['data']['confidence'] = .9
        with self.assertRaises(ValueError):
            self.s.apply(self.event)
        self.event['event_id'] = 'bad'
        self.event['data']['id'] = []
        with self.assertRaises(ValueError):
            self.s.apply(self.event)
        with self.assertRaises(ValueError):
            self.s.seed([{'id': 'bad', 'target': 'survivor', 'priority': 'high'}])


if __name__ == '__main__':
    unittest.main()
