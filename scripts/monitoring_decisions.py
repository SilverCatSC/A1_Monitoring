"""Record/revoke explicit owner decisions for future monitoring summaries."""

import argparse
import fcntl
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.service.analytical_summary import DECISIONS_FILE, _json_file, validate_decisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('year', 'partner', 'question'):
        part = sub.add_parser(command)
        part.add_argument('--unique-id', required=True)
        part.add_argument('--basis', required=True)
        part.add_argument('--confirmed-by', required=True)
        part.add_argument('--source', choices=('all', 'auto_ru', 'avito'), default='all')
        part.add_argument('--valid-until')
        if command == 'year':
            part.add_argument('--value', type=int, required=True)
        elif command == 'partner':
            part.add_argument('--partner', required=True)
        else:
            part.add_argument('--question', required=True)
    revoke = sub.add_parser('revoke')
    revoke.add_argument('--id', required=True)
    revoke.add_argument('--basis', required=True)
    sub.add_parser('list')
    args = parser.parse_args()
    root = Path(args.evidence_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / DECISIONS_FILE
    lock_path = root / '.monitoring_decisions.lock'
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        payload = _json_file(target, root)[0] if target.exists() or target.is_symlink() else {'schema_version': 1, 'entries': []}
        validate_decisions(payload)
        if args.command == 'list':
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'revoke':
            selected = [e for e in payload['entries'] if e['id'] == args.id]
            if len(selected) != 1:
                raise ValueError('decision not found')
            selected[0].update(active=False, revoked_at_utc=datetime.now(UTC).isoformat(), revocation_basis=args.basis)
        else:
            entry = {'id': str(uuid.uuid4()), 'unique_id': args.unique_id, 'source': args.source,
                     'kind': {'year': 'confirmed_year', 'partner': 'placement_exception', 'question': 'open_question'}[args.command],
                     'basis': args.basis, 'confirmed_by': args.confirmed_by,
                     'recorded_on': datetime.now(UTC).date().isoformat(), 'active': True}
            field = {'year': 'value', 'partner': 'partner', 'question': 'question'}[args.command]
            entry[field] = getattr(args, field)
            if args.valid_until:
                entry['valid_until'] = args.valid_until
            payload['entries'].append(entry)
        validate_decisions(payload)
        fd, temporary = tempfile.mkstemp(prefix='.decisions-', dir=root)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    print('MONITORING_DECISION_SAVED source_writes=false')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
