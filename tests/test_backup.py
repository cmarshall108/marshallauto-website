import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app, db
from app.backup import (
    BackupError,
    create_backup,
    decrypt_file,
    encrypt_file,
    restore_backup,
)
from app.models import User
from config import TestingConfig


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.public = root / 'public'
        self.private = root / 'private'
        self.db_path = root / 'data.sqlite'
        self.local_backup = root / 'encrypted-local'
        self.output = root / 'out'
        self.static = root / 'static'
        self.stock = self.static / 'images' / 'coming-soon-stock'
        for path in (self.public / 'vehicles', self.public / 'carfax', self.private / 'licenses',
                     self.stock, self.local_backup, self.output):
            path.mkdir(parents=True, exist_ok=True)

        # Seed media and private docs that must round-trip.
        (self.public / 'vehicles' / 'car.jpg').write_bytes(b'fake-jpeg-bytes')
        (self.public / 'carfax' / 'report.pdf').write_bytes(b'%PDF-fake')
        (self.private / 'licenses' / 'buyer.bin').write_bytes(b'secret-license')
        (self.stock / 'demo.webp').write_bytes(b'RIFF-webp-demo')

        # File-backed SQLite so the backup dump has real content.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('create table ping (id integer primary key, note text)')
            conn.execute("insert into ping(note) values ('alive')")
            conn.commit()

        self.encryption_key = 'test-backup-key-not-for-production-use-32b'
        config = type('BackupTestConfig', (TestingConfig,), {
            'UPLOAD_FOLDER': str(self.public),
            'PRIVATE_UPLOAD_FOLDER': str(self.private),
            'SQLALCHEMY_DATABASE_URI': f'sqlite:///{self.db_path}',
            'BACKUP_ENCRYPTION_KEY': self.encryption_key,
            'BACKUP_ENABLED': True,
            'BACKUP_GIT_URL': '',
            'BACKUP_GITHUB_TOKEN': '',
            'BACKUP_LOCAL_DIR': str(self.local_backup),
            'BACKUP_INCLUDE_PUBLIC_UPLOADS': True,
            'BACKUP_INCLUDE_COMING_SOON_STOCK': True,
            'BACKUP_RETENTION_COUNT': 5,
        })
        self.app = create_app(config)
        # Point static folder at our temp tree for coming-soon stock restore.
        self.app.static_folder = str(self.static)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username='backup-admin')
        user.set_password('test-password-only')
        db.session.add(user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp.cleanup()

    def test_encrypt_decrypt_round_trip_and_tamper_detection(self):
        source = Path(self.tmp.name) / 'plain.bin'
        source.write_bytes(os.urandom(1500_000))  # multi-chunk
        encrypted = Path(self.tmp.name) / 'plain.mbak'
        restored = Path(self.tmp.name) / 'plain.out'
        meta = encrypt_file(source, encrypted)
        self.assertGreater(meta['chunks'], 1)
        digest = decrypt_file(encrypted, restored)
        self.assertEqual(digest, meta['sha256_plaintext'])
        self.assertEqual(source.read_bytes(), restored.read_bytes())

        # Bit-flip must fail closed.
        blob = bytearray(encrypted.read_bytes())
        blob[-70] ^= 0x5A
        tampered = Path(self.tmp.name) / 'tampered.mbak'
        tampered.write_bytes(blob)
        with self.assertRaises(BackupError):
            decrypt_file(tampered, Path(self.tmp.name) / 'nope.bin')

    def test_create_backup_without_push_and_restore_apply(self):
        result = create_backup(push=False, output_dir=self.output)
        artifact = Path(result['local_path'])
        self.assertTrue(artifact.is_file())
        self.assertTrue(artifact.name.endswith('.mbak'))
        self.assertTrue((self.local_backup / artifact.name).is_file())
        # Ciphertext should not contain obvious plaintext markers.
        raw = artifact.read_bytes()
        self.assertNotIn(b'secret-license', raw)
        self.assertNotIn(b'fake-jpeg-bytes', raw)

        # Wipe live data then restore.
        shutil.rmtree(self.private)
        shutil.rmtree(self.public)
        shutil.rmtree(self.stock)
        self.private.mkdir(parents=True)
        self.public.mkdir(parents=True)
        self.db_path.write_bytes(b'corrupted')

        summary = restore_backup(artifact, apply=True)
        self.assertTrue(summary['restored']['database']['applied'])
        self.assertTrue(summary['restored']['private_uploads']['applied'])
        self.assertTrue(summary['restored']['public_uploads']['applied'])
        self.assertTrue(summary['restored']['coming_soon_stock']['applied'])

        self.assertEqual((self.private / 'licenses' / 'buyer.bin').read_bytes(), b'secret-license')
        self.assertEqual((self.public / 'vehicles' / 'car.jpg').read_bytes(), b'fake-jpeg-bytes')
        self.assertEqual((self.public / 'carfax' / 'report.pdf').read_bytes(), b'%PDF-fake')
        self.assertEqual((self.stock / 'demo.webp').read_bytes(), b'RIFF-webp-demo')

        with sqlite3.connect(self.db_path) as conn:
            note = conn.execute('select note from ping').fetchone()[0]
        self.assertEqual(note, 'alive')

    def test_restore_dry_run_does_not_write(self):
        result = create_backup(push=False, output_dir=self.output)
        marker = self.private / 'licenses' / 'buyer.bin'
        original = marker.read_bytes()
        marker.write_bytes(b'changed-before-dry-run')
        summary = restore_backup(Path(result['local_path']), apply=False)
        self.assertFalse(summary['applied'])
        self.assertEqual(marker.read_bytes(), b'changed-before-dry-run')
        # Re-apply restores the backup payload.
        restore_backup(Path(result['local_path']), apply=True)
        self.assertEqual(marker.read_bytes(), original)

    def test_cli_backup_data_no_push(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(
            args=['backup-data', '--no-push', '--output-dir', str(self.output)],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Created encrypted backup', result.output)
        self.assertTrue(any(self.output.glob('*.mbak')))

    def test_publish_uses_local_git_repo(self):
        remote = Path(self.tmp.name) / 'remote.git'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        with patch.dict(self.app.config, {
            'BACKUP_GIT_URL': str(remote),
            'BACKUP_GITHUB_TOKEN': '',
            'BACKUP_GIT_BRANCH': 'main',
        }):
            result = create_backup(push=True, output_dir=self.output)
        self.assertTrue(result['pushed'])
        publish = result['publish']
        self.assertTrue(str(publish['artifact']).endswith('.mbak'))

        # Clone remote and ensure only ciphertext + metadata landed.
        mirror = Path(self.tmp.name) / 'mirror'
        subprocess.run(
            ['git', 'clone', str(remote), str(mirror)],
            check=True, capture_output=True,
        )
        mbak_files = list(mirror.glob('backups/**/*.mbak'))
        self.assertEqual(len(mbak_files), 1)
        meta_files = list(mirror.glob('backups/**/*.mbak.json'))
        self.assertEqual(len(meta_files), 1)
        meta = json.loads(meta_files[0].read_text(encoding='utf-8'))
        self.assertTrue(meta['encrypted'])
        self.assertEqual(meta['ciphertext_sha256'], result['sha256_ciphertext'])
        self.assertNotIn(b'secret-license', mbak_files[0].read_bytes())

        # Restore from the published artifact.
        shutil.rmtree(self.private)
        self.private.mkdir()
        restore_backup(mbak_files[0], apply=True, restore_public=False, restore_stock=False)
        self.assertEqual((self.private / 'licenses' / 'buyer.bin').read_bytes(), b'secret-license')

    def test_wrong_key_fails(self):
        result = create_backup(push=False, output_dir=self.output)
        with patch.dict(self.app.config, {'BACKUP_ENCRYPTION_KEY': 'totally-different-key-value'}):
            with self.assertRaises(BackupError):
                restore_backup(Path(result['local_path']), apply=False)


if __name__ == '__main__':
    unittest.main()
