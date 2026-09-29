import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from nuc.latest_jsonl import LatestJSONL
from reproduction.portal.pilot_health import HealthJournal
from reproduction.portal.record_manual_pilot import LocalTail


def append(path, row):
    with path.open('ab') as stream:
        stream.write(json.dumps(row).encode() + b'\n')


def test_backlog_reads_only_latest_complete_record(tmp_path):
    path = tmp_path / 'events.jsonl';path.touch()
    reader = LatestJSONL(path, chunk_bytes=128)
    with path.open('ab') as stream:
        for index in range(10000):
            stream.write(json.dumps(dict(index=index, payload='x'*1000)).encode() + b'\n')
        stream.write(b'{"index": 10000')
    original = json.loads
    with patch('nuc.latest_jsonl.json.loads', wraps=original) as decode:
        assert reader.poll()['index'] == 9999
        assert decode.call_count == 1
    assert reader.poll() is None
    with path.open('ab') as stream:stream.write(b'}\n')
    assert reader.poll() == {'index':10000}
    assert reader.poll() is None
    reader.close()


def test_partial_initial_record_is_not_parsed_as_a_new_message(tmp_path):
    path = tmp_path / 'events.jsonl';path.write_bytes(b'{"initial":')
    reader = LatestJSONL(path, chunk_bytes=16)
    with path.open('ab') as stream:stream.write(b' 1}\n')
    assert reader.poll() is None
    append(path, {'new': 2})
    assert reader.poll() == {'new': 2}
    reader.close()


def test_truncation_and_oversized_records_fail_closed(tmp_path):
    path = tmp_path / 'events.jsonl';append(path, {'initial':1})
    reader = LatestJSONL(path, chunk_bytes=16, max_bytes=32)
    append(path, {'payload':'x'*200})
    with pytest.raises(ValueError, match='capacity'):reader.poll()
    path.write_bytes(b'')
    with pytest.raises(ValueError, match='truncated'):reader.poll()
    reader.close()


def test_producer_receipt_timestamp_survives_delayed_poll_and_partial_append(tmp_path):
    path = tmp_path / 'events.jsonl';path.touch()
    reader = LocalTail(path)
    append(path, {'phase':'ready', 'pc_received_at':100.})
    with patch('reproduction.portal.record_manual_pilot.time.monotonic',return_value=101.):
        row = reader.poll()
        assert row['pc_received_at'] == 100.  # Already stale, not magically fresh at 101.
        with path.open('ab') as stream:stream.write(b'{"phase":')
        assert reader.poll() is row
    reader.close()


def sample(now):
    return dict(arm=dict(pc_received_at=now, data=dict(source_read_at=40.,
        sample=dict(state=dict(received_at=39.99),pilot=dict(mode='locked',command='lock',command_id=2)))),
        control=dict(pc_received_at=now),gripper=dict(pc_received_at=now),cameras={})


def test_fault_evidence_survives_recovery_without_repeated_log_spam(tmp_path):
    path = tmp_path / 'health.jsonl';journal = HealthJournal(path)
    journal.observe({}, 100., 'arm: waiting for data',loop_gap=.02,snapshot_seconds=.001)
    assert not path.exists()  # Expected initial connection, not an incident.
    journal.observe(sample(101.),101.,None,loop_gap=.02,snapshot_seconds=.001)
    journal.observe(sample(101.),101.6,'arm: stale stream',loop_gap=.6,snapshot_seconds=.001)
    journal.observe(sample(101.),101.7,'arm: stale stream',loop_gap=.1,snapshot_seconds=.001)
    journal.observe(sample(102.),102.,None,loop_gap=.02,snapshot_seconds=.001)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert [r['kind'] for r in rows] == ['feedback_fault','feedback_recovered']
    assert rows[0]['ages']['arm'] == pytest.approx(.6)
    assert len(rows[0]['preceding']) == 3
    assert journal.status()['last_fault']['reason'] == 'arm: stale stream'
    assert journal.status()['recovered_unix'] is not None


def test_diagnostic_write_failure_does_not_replace_control_failure(tmp_path):
    journal = HealthJournal(tmp_path / 'missing/health.jsonl')
    journal.observe(sample(101.),101.,None,loop_gap=.02,snapshot_seconds=.001)
    journal.observe(sample(101.),102.,'arm: stale stream',loop_gap=1.,snapshot_seconds=.001)
    assert journal.status()['last_fault']['reason'] == 'arm: stale stream'
    assert journal.status()['journal_error']
