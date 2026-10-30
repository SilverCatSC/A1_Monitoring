"""Publish a recoverable local artifact set with a completion manifest last."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_bundle(directory, marker, *, required=()):
    directory = Path(directory)
    target = directory / marker
    if target.is_symlink() or not target.is_file() or target.stat().st_size > 32000:
        raise ValueError('bundle is not committed')
    manifest = json.loads(target.read_bytes())
    files = manifest.get('files') if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or not isinstance(files, dict) or not files
            or len(files) > 10 or not set(required) <= files.keys()):
        raise ValueError('invalid bundle manifest')
    for name, expected in files.items():
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in {'.', '..'}
                or not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected)):
            raise ValueError('invalid bundle member')
        path = directory / name
        if path.is_symlink() or not path.is_file() or _digest(path.read_bytes()) != expected:
            raise ValueError('bundle member is missing or changed')
    return manifest


def publish_bundle(directory, members, *, marker, commit_member):
    """No overwrite. Retry identical interrupted output using the pending manifest.

    Readers accept the set only after verify_bundle succeeds. On a normal I/O
    exception, newly published members are rolled back; foreign files are kept.
    A hard-killed writer leaves a matching pending manifest for safe resumption.
    """
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('invalid output directory')
    directory.mkdir(parents=True, exist_ok=True)
    if (commit_member not in members or len(members) > 10 or marker in members
            or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', n) or n in {'.', '..'} for n in [marker, *members])):
        raise ValueError('invalid artifact names')
    data = {name: value if isinstance(value, bytes) else value.encode('utf-8') for name, value in members.items()}
    manifest = {'schema_version': 1, 'files': {name: _digest(value) for name, value in data.items()}}
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + '\n').encode()
    ready, pending = directory / marker, directory / ('.' + marker + '.pending')
    fd = os.open(directory / '.artifact-bundle.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if ready.exists() or ready.is_symlink():
            if verify_bundle(directory, marker, required=members) != manifest:
                raise FileExistsError('different bundle is already committed')
            return
        recovering = pending.exists() or pending.is_symlink()
        if recovering and (pending.is_symlink() or pending.read_bytes() != manifest_bytes):
            raise FileExistsError('different pending bundle')
        for name, value in data.items():
            target = directory / name
            if target.exists() or target.is_symlink():
                if not recovering or target.is_symlink() or target.read_bytes() != value:
                    raise FileExistsError('existing artifact is not this bundle')
        stage = Path(tempfile.mkdtemp(prefix='.bundle-stage-', dir=directory))
        created = []
        pending_created = False
        try:
            for name, value in data.items():
                _write(stage / name, value)
            _write(stage / marker, manifest_bytes)
            if not recovering:
                _write(pending, manifest_bytes)
                pending_created = True
                _sync(directory)
            ordered = [n for n in data if n != commit_member] + [commit_member]
            for name in ordered:
                target = directory / name
                if not target.exists():
                    os.link(stage / name, target)
                    created.append(target)
            os.link(stage / marker, ready)
            created.append(ready)
            _sync(directory)
            pending.unlink()
        except BaseException:
            for target in reversed(created):
                target.unlink(missing_ok=True)
            if pending_created:
                pending.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(stage)
