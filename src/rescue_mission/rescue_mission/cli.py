import argparse
import json
from pathlib import Path
from .store import Store


def main():
    parser = argparse.ArgumentParser(description='Inspect and test mission memory without ROS')
    parser.add_argument('--db', default='~/.local/share/rescue_mission/practice.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init').add_argument('objectives')
    sub.add_parser('ingest').add_argument('event_file')
    sub.add_parser('status')
    sub.add_parser('next')
    sub.add_parser('issue').add_argument('request_id')
    sub.add_parser('export').add_argument('output')
    args = parser.parse_args()
    store = Store(args.db)
    try:
        if args.command == 'init':
            store.seed(json.loads(Path(args.objectives).read_text(encoding='utf-8')))
            result = store.snapshot()
        elif args.command == 'ingest':
            result = {'accepted': store.apply(json.loads(Path(args.event_file).read_text(encoding='utf-8')))}
        elif args.command == 'next':
            result = store.propose()
        elif args.command == 'issue':
            result = store.issue(args.request_id)
        else:
            result = store.snapshot()
            if args.command == 'export':
                Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
