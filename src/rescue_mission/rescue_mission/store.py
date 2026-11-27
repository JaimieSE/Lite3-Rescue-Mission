"""ROS-independent mission memory. One SQLite database per attempt."""
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


class Store:
    def __init__(self, path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS records (
              kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY, received TEXT NOT NULL, data TEXT NOT NULL);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, kind, key):
        row = self.db.execute('SELECT data FROM records WHERE kind=? AND id=?',
                              (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def rows(self, kind):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT data FROM records WHERE kind=? ORDER BY id', (kind,))]

    def put(self, kind, value):
        self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                        (kind, value['id'], json.dumps(value, allow_nan=False)))

    def seed(self, objectives):
        # Re-running startup must never erase completed objectives.
        with self.db:
            for obj in objectives:
                if not nonempty(obj.get('id')) or not nonempty(obj.get('target')):
                    raise ValueError('Each objective needs id and target')
                self.validate_objective(obj)
                if not self.get('objective', obj['id']):
                    self.put('objective', dict(obj, status='pending', findings=[]))

    @staticmethod
    def valid_location(location):
        return (isinstance(location, dict) and location.get('frame_id') == 'map'
                and all(isinstance(location.get(k), (int, float))
                        and not isinstance(location[k], bool)
                        and math.isfinite(location[k]) for k in ('x', 'y', 'z')))

    def apply(self, event):
        """Atomic, idempotent ingestion. Invalid events leave no partial changes."""
        if not isinstance(event, dict):
            raise ValueError('Event must be a JSON object')
        for field in ('event_id', 'type', 'source', 'observed_at'):
            if not nonempty(event.get(field)):
                raise ValueError('Missing string field: ' + field)
        # Timestamp is producer observation time, not receipt time.
        datetime.fromisoformat(event['observed_at'].replace('Z', '+00:00'))
        with self.db:
            previous = self.db.execute('SELECT data FROM events WHERE id=?',
                                       (event['event_id'],)).fetchone()
            if previous:
                if json.loads(previous[0]) != event:
                    raise ValueError('event_id reused with different content')
                return False
            kind = event['type']
            data = event.get('data')
            if not isinstance(data, dict):
                raise ValueError('data must be an object')
            if not nonempty(data.get('id')):
                raise ValueError('data.id must be a nonempty string')
            if kind == 'observation':
                self._observation(data, event)
            elif kind == 'qr':
                for key in ('id', 'text', 'evidence_ref'):
                    if not nonempty(data.get(key)):
                        raise ValueError('QR requires ' + key)
                self.put('qr', dict(data, source=event['source'],
                                    observed_at=event['observed_at']))
            elif kind == 'objective':
                # Trusted mission parser/operator only; raw QR/VLM text is never executed.
                self.seed_in_transaction(data)
            elif kind == 'objective_status':
                obj = self.get('objective', data.get('id'))
                if not obj or data.get('status') not in ('pending', 'blocked'):
                    raise ValueError('Use verified observations for completion')
                obj.update(status=data['status'], reason=data.get('reason', ''))
                self.put('objective', obj)
            elif kind == 'search_complete':
                obj = self.get('objective', data.get('id'))
                if not obj or obj.get('mode') != 'collection' or not nonempty(data.get('evidence_ref')):
                    raise ValueError('Collection closure requires coverage evidence')
                obj.update(status='complete', coverage_evidence=data['evidence_ref'])
                self.put('objective', obj)
            elif kind == 'action_result':
                req = self.get('request', data.get('id'))
                if not req or req['status'] != 'pending':
                    raise ValueError('Result must reference an outstanding request')
                if data.get('status') not in ('succeeded', 'failed', 'cancelled'):
                    raise ValueError('Invalid action status')
                req.update(status=data['status'], detail=data.get('detail', ''))
                self.put('request', req)
                if data['status'] in ('failed', 'cancelled'):
                    obj = self.get('objective', req['objective_id'])
                    if obj['status'] != 'complete':
                        obj.update(status='blocked', reason=req['detail'] or data['status'])
                        self.put('objective', obj)
            else:
                raise ValueError('Unsupported event type: ' + kind)
            self.db.execute('INSERT INTO events VALUES (?,?,?)',
                            (event['event_id'], now(), json.dumps(event, allow_nan=False)))
        return True

    def seed_in_transaction(self, obj):
        if not nonempty(obj.get('id')) or not nonempty(obj.get('target')):
            raise ValueError('Objective requires id and target')
        if self.get('objective', obj['id']):
            raise ValueError('Objective id already exists')
        self.validate_objective(obj)
        self.put('objective', dict(obj, status='pending', findings=[]))

    @staticmethod
    def validate_objective(obj):
        priority = obj.get('priority', 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError('priority must be an integer')
        if obj.get('mode', 'single') not in ('single', 'collection'):
            raise ValueError('mode must be single or collection')
        for key in ('room', 'relation'):
            if key in obj and not nonempty(obj[key]):
                raise ValueError(key + ' must be a nonempty string')

    def _observation(self, data, event):
        for key in ('id', 'entity_id', 'label', 'evidence_ref'):
            if not nonempty(data.get(key)):
                raise ValueError('Observation requires ' + key)
        confidence = data.get('confidence')
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError('confidence must be between 0 and 1')
        if self.get('observation', data['id']):
            raise ValueError('Observation id already exists; use a new view id')
        # verified is supplied by a trusted evidence validator, never raw model confidence.
        confirmed = (data.get('verified') is True and confidence >= 0.8
                     and self.valid_location(data.get('location')))
        obs = dict(data, confirmed=confirmed, source=event['source'],
                   observed_at=event['observed_at'])
        self.put('observation', obs)
        if not confirmed:
            return
        for obj in self.rows('objective'):
            if obj['status'] == 'complete' or obj['target'] != data['label']:
                continue
            if obj.get('room') and obj['room'] != data.get('room'):
                continue
            if obj.get('relation') and (obj['relation'] != data.get('relation')
                                       or not nonempty(data.get('relation_reference'))):
                continue
            if obj['target'] == 'medical_supplies' and not nonempty(data.get('room')):
                continue
            if obj['target'] == 'electrical_hazard' and not nonempty(data.get('warning_symbol')):
                continue
            if data['entity_id'] not in obj['findings']:
                obj['findings'].append(data['entity_id'])
            obj['status'] = 'active' if obj.get('mode') == 'collection' else 'complete'
            self.put('objective', obj)

    def snapshot(self):
        return {kind: self.rows(kind) for kind in
                ('objective', 'observation', 'qr', 'request')}

    def propose(self):
        """Produce an advisory request; never sends a motor/Nav2/launch command."""
        outstanding = [r for r in self.rows('request') if r['status'] == 'pending']
        if outstanding:
            return outstanding[0]
        remaining = sorted((o for o in self.rows('objective')
                            if o['status'] in ('pending', 'active')),
                           key=lambda o: (-o.get('priority', 0), o['id']))
        if not remaining:
            return {'action': 'review', 'reason': 'Complete or blocked tasks; review report and handoff readiness'}
        obj = remaining[0]
        observations = self.rows('observation')
        confirmed_entities = {o['entity_id'] for o in observations if o['confirmed']}
        candidates = [o for o in observations if not o['confirmed']
                      and o['entity_id'] not in confirmed_entities
                      and o['label'] == obj['target']
                      and (not obj.get('room') or o.get('room') == obj['room'])]
        action = 'inspect' if candidates else 'explore'
        return {'action': action, 'objective_id': obj['id'],
                'target': obj['target'], 'room': obj.get('room'),
                'candidate_id': candidates[0]['id'] if candidates else None,
                'reason': 'Verify candidate' if candidates else 'Search for supporting observations'}

    def issue(self, request_id):
        with self.db:
            if self.get('request', request_id):
                raise ValueError('Request id already exists')
            req = self.propose()
            if req.get('status') == 'pending' or req['action'] == 'review':
                return req
            req.update(id=request_id, status='pending', created_at=now())
            self.put('request', req)
            return req
