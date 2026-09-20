import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = load('agentbridge_backup_export')
pull = load('agentbridge_backup_pull')


class BackupReplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.destination = self.root / 'destination'
        self.generation = 'agentbridge-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-abcdef01'
        self.archive = self.source / (self.generation + '.zip')
        self.archive.write_bytes(b'opaque encrypted backup fixture')
        self.manifest = self.source / (self.generation + '.manifest.json')
        self.manifest.write_text(json.dumps({'schemaVersion':'agentbridge.recovery-bundle.v2',
                                           'archiveFile': self.archive.name, 'sha256': export.digest(self.archive)}))

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, command, path, size, digest):
        output = io.BytesIO()
        export.serve(command, self.source, output)
        Path(path).write_bytes(output.getvalue())
        self.assertEqual(len(output.getvalue()), size)
        self.assertEqual(pull.digest(path), digest)

    def collect(self, **kwargs):
        return pull.collect(str(self.destination), self.fetch, kwargs.get('listing', export.inventory(self.source)))

    def test_pull_reuses_verified_generation_and_source_deletion_does_not_delete_copy(self):
        self.assertEqual(self.collect()['copied'], 1)
        self.assertEqual(self.collect()['reused'], 1)
        self.archive.unlink()
        self.manifest.unlink()
        with self.assertRaises(ValueError):
            self.collect(listing=[])
        self.assertTrue((self.destination / self.generation / self.archive.name).is_file())

    def test_export_denies_arbitrary_commands_paths_and_unfinished_generations(self):
        for command in ('id', 'get ../../config/session.key archive', 'get ' + self.generation + ' key',
                        'get ' + self.generation + ' archive;id', 'list; id'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                export.serve(command, self.source, io.BytesIO())
        self.manifest.unlink()
        with self.assertRaises(ValueError):
            export.serve('get ' + self.generation + ' archive', self.source, io.BytesIO())

    def test_changed_source_generation_cannot_replace_saved_history(self):
        self.collect()
        before = (self.destination / self.generation / self.archive.name).read_bytes()
        self.archive.write_bytes(b'changed')
        value = json.loads(self.manifest.read_text())
        value['sha256'] = export.digest(self.archive)
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
            self.collect()
        self.assertEqual((self.destination / self.generation / self.archive.name).read_bytes(), before)

    def test_interrupted_transfer_does_not_publish_partial_generation(self):
        def fail(command, path, size, digest):
            Path(path).write_bytes(b'partial')
            raise OSError('interrupted')
        with self.assertRaises(OSError):
            pull.collect(str(self.destination), fail, export.inventory(self.source))
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_transport_rejects_corrupt_content(self):
        process = unittest.mock.Mock()
        process.stdout = io.BytesIO(b'corrupt')
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch.object(pull.subprocess, 'Popen', return_value=process), self.assertRaises(ValueError):
            pull.fetch('get x archive', str(self.root / 'partial'), 7, '0' * 64)

    def test_stale_backup_is_not_reported_as_current_success(self):
        self.collect()
        with patch.object(pull.time, 'time', return_value=time.time() + 40 * 3600):
            with self.assertRaisesRegex(ValueError, 'stale'):
                self.collect()

    def test_inventory_rejects_cross_directory_archive(self):
        value = json.loads(self.manifest.read_text())
        value['archiveFile'] = '../session.key'
        self.manifest.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            export.inventory(self.source)

    def test_retention_keeps_seven_and_ignores_non_generation_directories(self):
        self.collect()
        old = []
        for i in range(8):
            p = self.destination / ('agentbridge-20200101T000000Z-%08x' % i)
            p.mkdir()
            import os
            os.utime(p, (1, 1))
            old.append(p)
        unrelated = self.destination / 'operator-files'
        unrelated.mkdir()
        result = self.collect()
        self.assertEqual(result['pruned'], 2)
        self.assertTrue(unrelated.is_dir())
        self.assertEqual(sum(p.exists() for p in old), 6)
