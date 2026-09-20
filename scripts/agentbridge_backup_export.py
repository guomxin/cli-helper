#!/usr/bin/env python3
"""Forced SSH command: export completed v2 backup files only, never secrets."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path('/home/guomao/agentbridge/backups')
GENERATION = re.compile(r'^agentbridge-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked(root, name):
    path = root / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root.resolve():
        raise ValueError('invalid backup file')
    return path


def inventory(root):
    result = []
    for path in sorted(root.glob('agentbridge-*.manifest.json')):
        stem = path.name[:-len('.manifest.json')]
        if not GENERATION.fullmatch(stem):
            continue
        path = checked(root, path.name)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('manifest too large')
        value = json.loads(path.read_text())
        if value.get('schemaVersion') != 'agentbridge.recovery-bundle.v2':
            continue
        if value.get('archiveFile') != stem + '.zip':
            raise ValueError('archive filename mismatch')
        archive = checked(root, stem + '.zip')
        result.append({'generation': stem, 'manifestSha256': digest(path),
                       'archiveSha256': value['sha256'], 'archiveBytes': archive.stat().st_size,
                       'manifestBytes': path.stat().st_size})
    return result


def serve(command, root=ROOT, output=None):
    output = output or sys.stdout.buffer
    parts = command.split()
    if parts == ['list']:
        output.write(json.dumps(inventory(root)).encode('ascii'))
        return
    if len(parts) != 3 or parts[0] != 'get' or not GENERATION.fullmatch(parts[1]) or parts[2] not in ('manifest', 'archive'):
        raise ValueError('only list or get of a completed generation is allowed')
    # The manifest is the commit marker. No arbitrary paths, subprocesses or shell.
    if parts[1] not in {item['generation'] for item in inventory(root)}:
        raise ValueError('generation unavailable')
    suffix = '.manifest.json' if parts[2] == 'manifest' else '.zip'
    with checked(root, parts[1] + suffix).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            output.write(chunk)


if __name__ == '__main__':
    try:
        import pwd
        # Even though sshd uses a root authorized_key, no export code runs as root.
        account = pwd.getpwnam('agentbridge')
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        serve(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    except Exception:
        sys.stderr.write('backup export denied or unavailable\n')
        sys.exit(1)
