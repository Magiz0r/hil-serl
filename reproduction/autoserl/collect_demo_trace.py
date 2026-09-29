"""Copy a finished episode's action trace over SSH; never start robot services."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from reproduction.autoserl.export_demo import PROTOCOL, checked_trace, read_rows
from reproduction.portal.pilot_dataset import validate_episode


def collect_trace(episode):
    episode = Path(episode).resolve()
    result = validate_episode(episode)
    metadata = json.loads((episode / 'episode.json').read_text())
    session = json.loads((episode.parent / 'session.json').read_text())
    config = session['control_configuration']
    if (result['status'] != 'complete' or result['outcome'] != 'success'
            or metadata.get('demo_protocol') != PROTOCOL or config.get('demo_protocol') != PROTOCOL):
        raise ValueError('label a completed AutoSERL-mode demo successful before collecting its trace')
    run = config['run_directory']
    if not re.fullmatch(r'/hil-serl-state/logs/manual-\d{8}T\d{6}Z-\d+', run):
        raise ValueError('unexpected NUC run directory')
    command_id = metadata['control_start_command_id']
    if type(command_id) is not int or command_id < 0:
        raise ValueError('invalid episode start command id')
    # Only read the dedicated runtime log on the NUC host. Pass Python source
    # via stdin, never interpolated into a shell command.
    remote_path = '/home/tasl/hil_serl_runtime_20260918/state/logs/' + Path(run).name + '/demo-actions.jsonl'
    source = ('import json\nfrom pathlib import Path\n'
              f'for line in Path({remote_path!r}).open():\n'
              '    row=json.loads(line)\n'
              f'    if row.get("episode_command_id") == {command_id!r}: print(line, end="")\n')
    completed = subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
        '-o', 'StrictHostKeyChecking=yes', 'FrankaNUC', 'python3', '-'],
        input=source, text=True, capture_output=True, timeout=30, check=True)
    rows = [json.loads(line) for line in completed.stdout.splitlines()]
    sequences = {row['source_input_seq'] for row in rows}
    inputs = [row for row in read_rows(Path(session['control_log_directory']) / 'input.jsonl')
              if row['seq'] in sequences]
    checked_trace(rows, inputs, command_id)
    artifacts = {'demo-actions.jsonl': rows, 'demo-inputs.jsonl': inputs}
    encoded = {name: ''.join(json.dumps(row, allow_nan=False) + '\n' for row in value)
               for name, value in artifacts.items()}
    # Re-running is allowed only for byte-identical evidence; no replacement.
    for name, data in encoded.items():
        path = episode / name
        if path.exists() and path.read_text() != data:
            raise ValueError('different trace already exists: ' + str(path))
    for name, data in encoded.items():
        path = episode / name
        if not path.exists():
            with path.open('x') as stream:
                stream.write(data)
    return dict(episode=str(episode), actions=len(rows)-1, terminal=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('episode', type=Path)
    args = parser.parse_args()
    print(json.dumps(collect_trace(args.episode), indent=2))


if __name__ == '__main__':
    main()
