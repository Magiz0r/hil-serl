#!/usr/bin/env python3
"""Verify a raw pilot episode or all finished episodes in a session directory."""
import argparse
import json
from pathlib import Path
from pilot_dataset import validate_episode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    paths = [args.directory] if (args.directory / 'episode.json').exists() else sorted(args.directory.glob('episode_*'))
    if not paths:
        parser.error('no pilot episodes found')
    for path in paths:
        print(json.dumps(validate_episode(path), ensure_ascii=False))


if __name__ == '__main__':
    main()
