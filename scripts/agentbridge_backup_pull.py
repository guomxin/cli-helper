#!/usr/bin/env python
"""Standalone pull collector; Python 2.7/3 compatible for the existing backup host."""
from __future__ import print_function
import calendar
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

ROOT = '/srv/agentbridge-backups/213'
CONFIG = '/etc/agentbridge-backup'
GENERATION = re.compile(r'^agentbridge-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z')
SSH = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
       '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
       '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
       '-o', 'UserKnownHostsFile=' + CONFIG + '/known_hosts',
       '-i', CONFIG + '/pull_ed25519', 'root@10.10.50.213']


def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            value.update(chunk)
    return value.hexdigest()


def fetch(command, target, expected_size, expected_hash):
    if expected_size < 0 or expected_size > 4 * 1024 ** 3:
        raise ValueError('backup exceeds transfer limit')
    process = subprocess.Popen(SSH + [command], stdout=subprocess.PIPE)
    try:
        total = 0
        with open(target, 'wb') as output:
            while True:
                chunk = process.stdout.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise ValueError('transfer size exceeded')
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if process.wait() != 0 or total != expected_size or digest(target) != expected_hash:
            raise ValueError('transfer failed size/hash verification')
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def collect(root=ROOT, fetcher=fetch, listing=None):
    if listing is None:
        listing = json.loads(subprocess.check_output(SSH + ['list']))
    if not isinstance(listing, list) or not listing or len(listing) > 2000:
        raise ValueError('invalid or empty source inventory')
    if not os.path.isdir(root):
        os.makedirs(root, 0o700)
    copied, reused = 0, 0
    for entry in listing:
        generation = entry['generation']
        if not GENERATION.match(generation) or GENERATION.match(generation).group(0) != generation:
            raise ValueError('invalid generation')
        destination = os.path.join(root, generation)
        if os.path.lexists(destination):
            if os.path.islink(destination) or not os.path.isdir(destination):
                raise ValueError('invalid existing destination')
            if (digest(os.path.join(destination, generation + '.zip')) != entry['archiveSha256'] or
                    digest(os.path.join(destination, generation + '.manifest.json')) != entry['manifestSha256']):
                raise ValueError('existing generation differs; refusing overwrite')
            reused += 1
            continue
        stage = tempfile.mkdtemp(prefix='.incoming-', dir=root)
        try:
            manifest_path = os.path.join(stage, generation + '.manifest.json')
            if entry['manifestBytes'] > 8 * 1024 * 1024:
                raise ValueError('manifest too large')
            fetcher('get ' + generation + ' manifest', manifest_path,
                    entry['manifestBytes'], entry['manifestSha256'])
            with open(manifest_path) as stream:
                manifest = json.load(stream)
            if (manifest.get('schemaVersion') != 'agentbridge.recovery-bundle.v2' or
                    manifest.get('archiveFile') != generation + '.zip' or
                    manifest.get('sha256') != entry['archiveSha256']):
                raise ValueError('manifest does not match inventory')
            fetcher('get ' + generation + ' archive', os.path.join(stage, generation + '.zip'),
                    entry['archiveBytes'], entry['archiveSha256'])
            os.rename(stage, destination)
            copied += 1
        finally:
            if os.path.isdir(stage):
                shutil.rmtree(stage)
    latest = max(item['generation'] for item in listing)
    latest_time = calendar.timegm(time.strptime(latest[12:28], '%Y%m%dT%H%M%SZ'))
    if time.time() - latest_time > 36 * 3600 or latest_time > time.time() + 3600:
        raise ValueError('latest backup is stale or clock is invalid; check source backup service')
    # Retention is local policy, not a mirror/delete request from the source.
    # Keep at least seven generations; prune only after a successful collection.
    generations = sorted(name for name in os.listdir(root) if GENERATION.match(name))
    cutoff = time.time() - 90 * 86400
    pruned = 0
    for name in generations[:-7]:
        path = os.path.join(root, name)
        if not os.path.islink(path) and os.path.isdir(path) and os.stat(path).st_mtime < cutoff:
            shutil.rmtree(path)
            pruned += 1
    report = {'status': 'succeeded', 'checkedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'copied': copied, 'reused': reused, 'pruned': pruned,
              'latestGeneration': max(item['generation'] for item in listing),
              'retentionDays': 90, 'minimumGenerations': 7}
    temporary = os.path.join(root, '.last-success.tmp')
    with open(temporary, 'w') as stream:
        json.dump(report, stream)
        stream.flush()
        os.fsync(stream.fileno())
    getattr(os, 'replace', os.rename)(temporary, os.path.join(root, 'last-success.json'))
    return report


if __name__ == '__main__':
    os.umask(0o077)
    print(json.dumps(collect()))
