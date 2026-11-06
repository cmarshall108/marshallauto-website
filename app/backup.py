"""Encrypted daily data backup to a private GitHub repository.

Creates a tar archive of application data (database dump, private uploads,
public vehicle/CarFax media), encrypts it with AES-256-GCM using a dedicated
key, and pushes only ciphertext to a separate private git remote. Restore
decrypts a chosen artifact and writes files back under controlled paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import click
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app
from flask.cli import with_appcontext

MAGIC = b'MABAK001'
FORMAT_VERSION = 1
CHUNK_SIZE = 1024 * 1024
DEFAULT_RETENTION = 30
_SAFE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.mbak$')


class BackupError(RuntimeError):
    """Raised when backup or restore cannot complete safely."""


def _utc_now():
    return datetime.now(timezone.utc)


def _bool_config(name, default=False):
    value = current_app.config.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _int_config(name, default):
    value = current_app.config.get(name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _project_root():
    return Path(current_app.root_path).resolve().parent


def backup_encryption_key_material() -> bytes:
    raw = (current_app.config.get('BACKUP_ENCRYPTION_KEY') or '').strip()
    if not raw:
        raise BackupError(
            'Set BACKUP_ENCRYPTION_KEY to a dedicated secret before running backups. '
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return raw.encode('utf-8')


def derive_backup_key(material: bytes | None = None) -> bytes:
    material = material if material is not None else backup_encryption_key_material()
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b'marshallauto-data-backup-v1',
        info=b'marshallauto-backup-aesgcm-v1',
    ).derive(material)


def _database_uri() -> str:
    return (current_app.config.get('SQLALCHEMY_DATABASE_URI') or '').strip()


def _sqlite_path_from_uri(uri: str) -> Path | None:
    """Resolve SQLite file paths the same way Flask-SQLAlchemy does.

    Relative ``sqlite:///file.db`` URIs land under ``app.instance_path``, not the
    process cwd/project root. Absolute ``sqlite:////abs/path.db`` URIs are kept as-is.
    """
    if not uri.startswith('sqlite'):
        return None
    if uri in ('sqlite:///:memory:', 'sqlite://'):
        return None
    if uri.startswith('sqlite:////'):
        return Path('/' + uri[len('sqlite:////'):])
    if uri.startswith('sqlite:///'):
        relative = uri[len('sqlite:///'):]
        path = Path(relative)
        if path.is_absolute():
            return path
        # Prefer the live Flask instance folder (production: <root>/instance/...).
        instance_path = Path(getattr(current_app, 'instance_path', '') or '')
        if instance_path.is_dir():
            return instance_path / path
        return _project_root() / 'instance' / path
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _write_sqlite_backup(destination: Path) -> dict:
    source = _sqlite_path_from_uri(_database_uri())
    if source is None:
        raise BackupError('SQLite database path could not be resolved from SQLALCHEMY_DATABASE_URI.')
    if not source.is_file():
        raise BackupError(f'SQLite database not found: {source}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
    return {
        'kind': 'sqlite',
        'source': str(source),
        'archive_path': 'database/marshall_auto.db',
        'sha256': _sha256_file(destination),
        'size': destination.stat().st_size,
    }


def _write_postgres_dump(destination: Path) -> dict:
    uri = _database_uri()
    destination.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    parsed = urlparse(uri)
    if parsed.password:
        env['PGPASSWORD'] = parsed.password
    command = [
        'pg_dump',
        '--format=custom',
        '--no-owner',
        '--no-acl',
        '--file', str(destination),
        uri,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=3600,
        )
    except FileNotFoundError as exc:
        raise BackupError('pg_dump is required to back up PostgreSQL databases.') from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError('pg_dump timed out while creating the database dump.') from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip()
        raise BackupError(f'pg_dump failed: {detail[-1000:]}')
    return {
        'kind': 'postgres',
        'source': f'{parsed.scheme}://{parsed.hostname}:{parsed.port or 5432}{parsed.path}',
        'archive_path': 'database/marshall_auto.dump',
        'sha256': _sha256_file(destination),
        'size': destination.stat().st_size,
    }


def dump_database(staging_dir: Path) -> dict:
    uri = _database_uri()
    if uri.startswith('sqlite'):
        target = staging_dir / 'database' / 'marshall_auto.db'
        return _write_sqlite_backup(target)
    if uri.startswith('postgresql') or uri.startswith('postgres'):
        target = staging_dir / 'database' / 'marshall_auto.dump'
        return _write_postgres_dump(target)
    raise BackupError(f'Unsupported database URI scheme for backup: {uri.split(":", 1)[0]}')


def _copy_tree(source: Path, destination: Path) -> list[dict]:
    entries: list[dict] = []
    if not source.exists():
        return entries
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        entries.append({
            'path': str(destination.name),
            'sha256': _sha256_file(destination),
            'size': destination.stat().st_size,
        })
        return entries
    for root, _dirs, files in os.walk(source):
        root_path = Path(root)
        for name in files:
            if name in {'.DS_Store', 'Thumbs.db'}:
                continue
            src_file = root_path / name
            rel = src_file.relative_to(source)
            dest_file = destination / rel
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest_file)
            entries.append({
                'path': str(rel).replace('\\', '/'),
                'sha256': _sha256_file(dest_file),
                'size': dest_file.stat().st_size,
            })
    return entries


def collect_payload(staging_dir: Path) -> dict:
    """Copy live data into a staging tree and return a plaintext manifest."""
    database_meta = dump_database(staging_dir)
    contents = {
        'database': database_meta,
        'private_uploads': [],
        'public_uploads': [],
        'coming_soon_stock': [],
    }

    private_src = Path(current_app.config['PRIVATE_UPLOAD_FOLDER'])
    private_dest = staging_dir / 'private_uploads'
    contents['private_uploads'] = _copy_tree(private_src, private_dest)

    if _bool_config('BACKUP_INCLUDE_PUBLIC_UPLOADS', True):
        upload_root = Path(current_app.config['UPLOAD_FOLDER'])
        for subdir in ('vehicles', 'carfax'):
            src = upload_root / subdir
            dest = staging_dir / 'public_uploads' / subdir
            copied = _copy_tree(src, dest)
            for item in copied:
                item['path'] = f'{subdir}/{item["path"]}'
            contents['public_uploads'].extend(copied)

    if _bool_config('BACKUP_INCLUDE_COMING_SOON_STOCK', True):
        stock_src = Path(current_app.static_folder) / 'images' / 'coming-soon-stock'
        stock_dest = staging_dir / 'coming_soon_stock'
        contents['coming_soon_stock'] = _copy_tree(stock_src, stock_dest)

    manifest = {
        'format_version': FORMAT_VERSION,
        'created_at': _utc_now().isoformat(),
        'hostname': socket.gethostname(),
        'site_url': current_app.config.get('SITE_URL'),
        'app_root': str(_project_root()),
        'contents': contents,
    }
    manifest_path = staging_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


def build_tar_archive(staging_dir: Path, archive_path: Path) -> str:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, 'w:gz') as tar:
        for child in sorted(staging_dir.iterdir(), key=lambda p: p.name):
            tar.add(child, arcname=child.name)
    return _sha256_file(archive_path)


def _chunk_nonce(base_nonce: bytes, index: int) -> bytes:
    """Derive a unique 96-bit GCM nonce per chunk from a random base nonce."""
    if len(base_nonce) != 12:
        raise BackupError('Backup nonce must be 12 bytes.')
    # 64-bit random prefix || 32-bit big-endian counter (must never reuse with same key).
    return base_nonce[:8] + index.to_bytes(4, 'big')


def encrypt_file(source: Path, destination: Path, *, key: bytes | None = None, aad: bytes = b'') -> dict:
    """Encrypt source to destination with chunked AES-256-GCM.

    Layout:
      MAGIC (8) | version u16 | base_nonce (12) | chunk_count u32 |
      repeated: chunk_len u32 | ciphertext+tag
      trailing: plaintext_sha256 hex ascii (64)
    """
    key = key if key is not None else derive_backup_key()
    base_nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    plaintext_sha = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    chunk_count = 0
    with source.open('rb') as src, destination.open('wb') as dst:
        dst.write(MAGIC)
        dst.write(FORMAT_VERSION.to_bytes(2, 'big'))
        dst.write(base_nonce)
        count_pos = dst.tell()
        dst.write((0).to_bytes(4, 'big'))
        while True:
            chunk = src.read(CHUNK_SIZE)
            if not chunk:
                break
            plaintext_sha.update(chunk)
            # Bind index in AAD so chunks cannot be reordered under the same key.
            chunk_aad = aad + chunk_count.to_bytes(4, 'big')
            encrypted = aesgcm.encrypt(_chunk_nonce(base_nonce, chunk_count), chunk, chunk_aad)
            dst.write(len(encrypted).to_bytes(4, 'big'))
            dst.write(encrypted)
            chunk_count += 1
        end = dst.tell()
        dst.seek(count_pos)
        dst.write(chunk_count.to_bytes(4, 'big'))
        dst.seek(end)
        digest = plaintext_sha.hexdigest().encode('ascii')
        dst.write(digest)
    return {
        'sha256_plaintext': plaintext_sha.hexdigest(),
        'sha256_ciphertext': _sha256_file(destination),
        'size': destination.stat().st_size,
        'chunks': chunk_count,
    }


def decrypt_file(source: Path, destination: Path, *, key: bytes | None = None, aad: bytes = b'') -> str:
    key = key if key is not None else derive_backup_key()
    aesgcm = AESGCM(key)
    plaintext_sha = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as src:
        magic = src.read(8)
        if magic != MAGIC:
            raise BackupError('Not a Marshall Auto backup file (bad magic).')
        version = int.from_bytes(src.read(2), 'big')
        if version != FORMAT_VERSION:
            raise BackupError(f'Unsupported backup format version: {version}')
        base_nonce = src.read(12)
        if len(base_nonce) != 12:
            raise BackupError('Backup file is truncated (nonce).')
        chunk_count = int.from_bytes(src.read(4), 'big')
        with destination.open('wb') as dst:
            for index in range(chunk_count):
                length_bytes = src.read(4)
                if len(length_bytes) != 4:
                    raise BackupError('Backup file is truncated (chunk length).')
                length = int.from_bytes(length_bytes, 'big')
                if length <= 16 or length > CHUNK_SIZE + 64:
                    raise BackupError('Backup chunk length is invalid.')
                payload = src.read(length)
                if len(payload) != length:
                    raise BackupError('Backup file is truncated (chunk body).')
                chunk_aad = aad + index.to_bytes(4, 'big')
                try:
                    plain = aesgcm.decrypt(_chunk_nonce(base_nonce, index), payload, chunk_aad)
                except InvalidTag as exc:
                    raise BackupError(
                        'Backup decryption failed. Check BACKUP_ENCRYPTION_KEY or file integrity.'
                    ) from exc
                plaintext_sha.update(plain)
                dst.write(plain)
            trailing = src.read(64)
            if len(trailing) != 64:
                raise BackupError('Backup file is missing plaintext checksum trailer.')
            expected = trailing.decode('ascii')
            actual = plaintext_sha.hexdigest()
            if expected != actual:
                raise BackupError('Decrypted backup failed plaintext checksum verification.')
            leftover = src.read(1)
            if leftover:
                raise BackupError('Backup file has unexpected trailing data.')
    return plaintext_sha.hexdigest()


def _authenticated_git_url(url: str, token: str | None) -> str:
    url = (url or '').strip()
    if not url:
        raise BackupError('Set BACKUP_GIT_URL to the private backup repository clone URL.')
    token = (token or '').strip() or None
    if not token:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return url
    # Prefer x-access-token for GitHub HTTPS; strip any existing userinfo.
    host_path = parsed.netloc.split('@')[-1]
    userinfo = f'x-access-token:{quote(token, safe="")}'
    netloc = f'{userinfo}@{host_path}'
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _run_git(args: list[str], *, cwd: Path, env: dict | None = None, check=True) -> subprocess.CompletedProcess:
    base_env = os.environ.copy()
    base_env.update({
        'GIT_TERMINAL_PROMPT': '0',
        'GC_COLOR': 'never',
    })
    if env:
        base_env.update(env)
    try:
        completed = subprocess.run(
            ['git', *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=base_env,
            check=False,
            timeout=1800,
        )
    except FileNotFoundError as exc:
        raise BackupError('git is required for remote backup repository operations.') from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f'git {" ".join(args)} timed out.') from exc
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip()
        raise BackupError(f'git {" ".join(args)} failed: {detail[-1500:]}')
    return completed


def _prepare_repo_clone(work_dir: Path) -> Path:
    repo_url = _authenticated_git_url(
        current_app.config.get('BACKUP_GIT_URL') or '',
        current_app.config.get('BACKUP_GITHUB_TOKEN') or '',
    )
    branch = (current_app.config.get('BACKUP_GIT_BRANCH') or 'main').strip() or 'main'
    repo_dir = work_dir / 'repo'
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    # Prefer clone; if the remote is empty/new, init and set remote.
    clone = _run_git(
        ['clone', '--depth', '50', '--branch', branch, repo_url, str(repo_dir)],
        cwd=work_dir,
        check=False,
    )
    if clone.returncode != 0:
        repo_dir.mkdir(parents=True, exist_ok=True)
        _run_git(['init', '-b', branch], cwd=repo_dir)
        _run_git(['remote', 'add', 'origin', repo_url], cwd=repo_dir)
        readme = repo_dir / 'README.md'
        if not readme.exists():
            readme.write_text(
                '# Marshall Auto encrypted data backups\n\n'
                'This private repository stores **encrypted** application data backups only.\n'
                'Ciphertext files use the `.mbak` extension. Keep `BACKUP_ENCRYPTION_KEY` offline;\n'
                'without it these files cannot be restored.\n',
                encoding='utf-8',
            )
            _run_git(['add', 'README.md'], cwd=repo_dir)
            _run_git(
                ['-c', 'user.name=Marshall Auto Backup', '-c', 'user.email=backup@localhost',
                 'commit', '-m', 'Initialize encrypted backup repository'],
                cwd=repo_dir,
                check=False,
            )
    else:
        _run_git(['checkout', branch], cwd=repo_dir, check=False)
    # Ensure commits can be created in CI/server environments without global git identity.
    _run_git(['config', 'user.name', 'Marshall Auto Backup'], cwd=repo_dir)
    _run_git(['config', 'user.email', 'backup@localhost'], cwd=repo_dir)
    return repo_dir


def _artifact_name(when: datetime | None = None) -> str:
    when = when or _utc_now()
    stamp = when.strftime('%Y%m%dT%H%M%SZ')
    host = re.sub(r'[^A-Za-z0-9._-]+', '-', socket.gethostname()).strip('-') or 'host'
    return f'marshallauto-{stamp}-{host}.mbak'


def _prune_old_backups(repo_dir: Path, keep: int) -> list[str]:
    keep = max(1, keep)
    artifacts = sorted(repo_dir.glob('backups/**/*.mbak'))
    # Sort by filename (UTC timestamp embedded) then mtime.
    artifacts.sort(key=lambda p: (p.name, p.stat().st_mtime))
    removed = []
    excess = artifacts[:-keep] if len(artifacts) > keep else []
    for path in excess:
        path.unlink(missing_ok=True)
        meta = path.with_suffix('.mbak.json')
        meta.unlink(missing_ok=True)
        removed.append(str(path.relative_to(repo_dir)))
    return removed


def publish_encrypted_backup(encrypted_path: Path, meta: dict) -> dict:
    work_root = Path(tempfile.mkdtemp(prefix='marshallauto-backup-git-'))
    try:
        repo_dir = _prepare_repo_clone(work_root)
        when = _utc_now()
        rel_dir = Path('backups') / when.strftime('%Y') / when.strftime('%m')
        target_dir = repo_dir / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        name = encrypted_path.name if _SAFE_NAME.match(encrypted_path.name) else _artifact_name(when)
        dest = target_dir / name
        shutil.copy2(encrypted_path, dest)
        meta_path = dest.with_suffix('.mbak.json')
        public_meta = {
            'filename': name,
            'created_at': meta.get('created_at') or when.isoformat(),
            'format_version': FORMAT_VERSION,
            'ciphertext_sha256': meta.get('sha256_ciphertext'),
            'plaintext_sha256': meta.get('sha256_plaintext'),
            'size_bytes': meta.get('size'),
            'chunks': meta.get('chunks'),
            'site_url': current_app.config.get('SITE_URL'),
            'hostname': socket.gethostname(),
            'database_kind': (meta.get('manifest') or {}).get('contents', {}).get('database', {}).get('kind'),
            'file_counts': {
                'private_uploads': len(
                    ((meta.get('manifest') or {}).get('contents') or {}).get('private_uploads') or []
                ),
                'public_uploads': len(
                    ((meta.get('manifest') or {}).get('contents') or {}).get('public_uploads') or []
                ),
                'coming_soon_stock': len(
                    ((meta.get('manifest') or {}).get('contents') or {}).get('coming_soon_stock') or []
                ),
            },
            'encrypted': True,
            'note': 'Payload is AES-256-GCM ciphertext. BACKUP_ENCRYPTION_KEY is required to restore.',
        }
        meta_path.write_text(json.dumps(public_meta, indent=2, sort_keys=True) + '\n', encoding='utf-8')

        retention = _int_config('BACKUP_RETENTION_COUNT', DEFAULT_RETENTION)
        removed = _prune_old_backups(repo_dir, retention)

        _run_git(['add', '-A'], cwd=repo_dir)
        status = _run_git(['status', '--porcelain'], cwd=repo_dir)
        if not status.stdout.strip():
            return {
                'committed': False,
                'reason': 'no-change',
                'artifact': str(rel_dir / name),
                'removed': removed,
            }
        message = f'Encrypted backup {name}'
        _run_git(['commit', '-m', message], cwd=repo_dir)
        branch = (current_app.config.get('BACKUP_GIT_BRANCH') or 'main').strip() or 'main'
        # Use HEAD:branch so a freshly initialized repo can create the remote branch.
        _run_git(['push', '-u', 'origin', f'HEAD:{branch}'], cwd=repo_dir)
        return {
            'committed': True,
            'artifact': str(rel_dir / name).replace('\\', '/'),
            'removed': removed,
            'branch': branch,
            'ciphertext_sha256': public_meta['ciphertext_sha256'],
        }
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def create_backup(*, push: bool = True, output_dir: Path | None = None) -> dict:
    """Build, encrypt, optionally publish a backup. Returns result metadata."""
    if not _bool_config('BACKUP_ENABLED', True):
        raise BackupError('Backups are disabled (BACKUP_ENABLED is false).')

    work = Path(tempfile.mkdtemp(prefix='marshallauto-backup-'))
    lasting_path = None
    try:
        staging = work / 'staging'
        staging.mkdir(parents=True, exist_ok=True)
        manifest = collect_payload(staging)
        archive = work / 'payload.tar.gz'
        plain_sha = build_tar_archive(staging, archive)
        name = _artifact_name()
        encrypted_tmp = work / name
        enc_meta = encrypt_file(archive, encrypted_tmp)
        if enc_meta['sha256_plaintext'] != plain_sha:
            raise BackupError('Internal error: archive checksum mismatch before publish.')

        if output_dir is not None:
            out_root = Path(output_dir)
            out_root.mkdir(parents=True, exist_ok=True)
            lasting_path = out_root / name
            shutil.copy2(encrypted_tmp, lasting_path)
        else:
            lasting_path = encrypted_tmp

        result = {
            'created_at': manifest['created_at'],
            'artifact_name': name,
            'local_path': str(lasting_path),
            'manifest': manifest,
            **enc_meta,
            'pushed': False,
        }
        if push:
            published = publish_encrypted_backup(lasting_path, result)
            result['pushed'] = bool(published.get('committed'))
            result['publish'] = published
        # Persist a copy under BACKUP_LOCAL_DIR when configured.
        local_dir = (current_app.config.get('BACKUP_LOCAL_DIR') or '').strip()
        if local_dir:
            dest_dir = Path(local_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            retained = dest_dir / name
            if lasting_path.resolve() != retained.resolve():
                shutil.copy2(lasting_path, retained)
            (dest_dir / f'{name}.json').write_text(
                json.dumps({
                    'created_at': result['created_at'],
                    'ciphertext_sha256': result['sha256_ciphertext'],
                    'plaintext_sha256': result['sha256_plaintext'],
                    'size': result['size'],
                }, indent=2) + '\n',
                encoding='utf-8',
            )
            result['local_retained_path'] = str(retained)
            result['local_path'] = str(retained)
        elif output_dir is None and not push:
            # Keep artifact outside the wiped work tree for the caller.
            hold = Path(tempfile.mkdtemp(prefix='marshallauto-backup-out-'))
            held = hold / name
            shutil.copy2(lasting_path, held)
            result['local_path'] = str(held)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    dest_root = destination.resolve()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            name = member.name
            if not name or name.startswith('/') or name.startswith('\\') or '..' in Path(name).parts:
                raise BackupError(f'Refusing unsafe tar member path: {name}')
            target = (destination / name).resolve()
            try:
                target.relative_to(dest_root)
            except ValueError as exc:
                raise BackupError(f'Tar member escapes restore directory: {name}') from exc
            if member.issym() or member.islnk():
                raise BackupError(f'Refusing tar member with link: {name}')
        # Python 3.12+ supports filter=; use it when available.
        try:
            tar.extractall(destination, filter='data')
        except TypeError:
            tar.extractall(destination)


def _replace_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        backup_side = destination.with_name(destination.name + f'.pre-restore-{int(_utc_now().timestamp())}')
        destination.rename(backup_side)
    if source.is_dir():
        shutil.copytree(source, destination)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def restore_database_from_extracted(extracted: Path, *, apply: bool) -> dict:
    db_dir = extracted / 'database'
    sqlite_file = db_dir / 'marshall_auto.db'
    dump_file = db_dir / 'marshall_auto.dump'
    result = {'applied': False, 'kind': None}
    if sqlite_file.is_file():
        result['kind'] = 'sqlite'
        target = _sqlite_path_from_uri(_database_uri())
        if target is None:
            # Default instance path when URI is in-memory during tests without override.
            target = _project_root() / 'instance' / 'marshall_auto.db'
        result['target'] = str(target)
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                side = target.with_name(target.name + f'.pre-restore-{int(_utc_now().timestamp())}')
                shutil.copy2(target, side)
                result['previous_copy'] = str(side)
            shutil.copy2(sqlite_file, target)
            result['applied'] = True
            result['sha256'] = _sha256_file(target)
        return result
    if dump_file.is_file():
        result['kind'] = 'postgres'
        result['dump'] = str(dump_file)
        if apply:
            uri = _database_uri()
            env = os.environ.copy()
            parsed = urlparse(uri)
            if parsed.password:
                env['PGPASSWORD'] = parsed.password
            try:
                completed = subprocess.run(
                    ['pg_restore', '--clean', '--if-exists', '--no-owner', '--no-acl', f'--dbname={uri}', str(dump_file)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=3600,
                )
            except FileNotFoundError as exc:
                raise BackupError('pg_restore is required to restore PostgreSQL dumps.') from exc
            if completed.returncode not in (0, 1):
                # pg_restore returns 1 for non-fatal warnings sometimes; treat >1 as failure.
                detail = (completed.stderr or completed.stdout or '').strip()
                raise BackupError(f'pg_restore failed: {detail[-1500:]}')
            result['applied'] = True
        return result
    raise BackupError('Extracted backup does not contain a database dump.')


def restore_backup(
    encrypted_path: Path,
    *,
    apply: bool = False,
    restore_private: bool = True,
    restore_public: bool = True,
    restore_stock: bool = True,
    restore_database: bool = True,
    work_dir: Path | None = None,
) -> dict:
    encrypted_path = Path(encrypted_path)
    if not encrypted_path.is_file():
        raise BackupError(f'Backup file not found: {encrypted_path}')

    cleanup = work_dir is None
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix='marshallauto-restore-'))
    try:
        archive = work / 'payload.tar.gz'
        digest = decrypt_file(encrypted_path, archive)
        extracted = work / 'extracted'
        _safe_extract_tar(archive, extracted)
        manifest_path = extracted / 'manifest.json'
        manifest = {}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

        summary = {
            'plaintext_sha256': digest,
            'manifest': manifest,
            'applied': apply,
            'restored': {},
        }

        if restore_database:
            summary['restored']['database'] = restore_database_from_extracted(extracted, apply=apply)

        if restore_private and (extracted / 'private_uploads').exists():
            dest = Path(current_app.config['PRIVATE_UPLOAD_FOLDER'])
            summary['restored']['private_uploads'] = {'target': str(dest), 'applied': False}
            if apply:
                _replace_tree(extracted / 'private_uploads', dest)
                os.chmod(dest, 0o700)
                summary['restored']['private_uploads']['applied'] = True

        if restore_public and (extracted / 'public_uploads').exists():
            dest = Path(current_app.config['UPLOAD_FOLDER'])
            summary['restored']['public_uploads'] = {'target': str(dest), 'applied': False}
            if apply:
                for subdir in ('vehicles', 'carfax'):
                    src = extracted / 'public_uploads' / subdir
                    if src.exists():
                        _replace_tree(src, dest / subdir)
                summary['restored']['public_uploads']['applied'] = True

        if restore_stock and (extracted / 'coming_soon_stock').exists():
            dest = Path(current_app.static_folder) / 'images' / 'coming-soon-stock'
            summary['restored']['coming_soon_stock'] = {'target': str(dest), 'applied': False}
            if apply:
                _replace_tree(extracted / 'coming_soon_stock', dest)
                summary['restored']['coming_soon_stock']['applied'] = True

        return summary
    finally:
        if cleanup:
            shutil.rmtree(work, ignore_errors=True)


def fetch_remote_backup(artifact: str | None = None, destination_dir: Path | None = None) -> Path:
    """Clone the backup repo and return a local path to the chosen (or latest) .mbak file."""
    work = Path(tempfile.mkdtemp(prefix='marshallauto-backup-fetch-'))
    repo_dir = _prepare_repo_clone(work)
    artifacts = sorted(repo_dir.glob('backups/**/*.mbak'), key=lambda p: p.name)
    if not artifacts:
        raise BackupError('No encrypted backups found in the remote repository.')
    if artifact:
        artifact_name = Path(artifact).name
        if not _SAFE_NAME.match(artifact_name):
            raise BackupError('Invalid backup artifact name.')
        matches = [p for p in artifacts if p.name == artifact_name or str(p).endswith(artifact)]
        if not matches:
            raise BackupError(f'Backup artifact not found in remote repo: {artifact}')
        chosen = matches[-1]
    else:
        chosen = artifacts[-1]
    dest_root = Path(destination_dir) if destination_dir else work
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / chosen.name
    shutil.copy2(chosen, dest)
    # Keep the temp clone only if destination_dir was provided (caller owns cleanup of work otherwise).
    if destination_dir is None:
        # Move file out, then delete clone workspace except we already copied to work root.
        lasting_parent = Path(tempfile.mkdtemp(prefix='marshallauto-backup-artifact-'))
        lasting = lasting_parent / chosen.name
        shutil.move(str(dest), str(lasting))
        shutil.rmtree(work, ignore_errors=True)
        return lasting
    shutil.rmtree(work, ignore_errors=True)
    return dest


def register_commands(app):
    @app.cli.command('backup-data')
    @click.option('--no-push', is_flag=True, help='Create and encrypt locally without pushing to GitHub.')
    @click.option(
        '--output-dir',
        type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
        default=None,
        help='Optional directory to write the encrypted .mbak artifact into.',
    )
    @with_appcontext
    def backup_data_command(no_push, output_dir):
        """Encrypt application data and commit ciphertext to the private backup repository."""
        if not _bool_config('BACKUP_ENABLED', True):
            click.echo('Backups are disabled (BACKUP_ENABLED is false); nothing to do.')
            return
        try:
            result = create_backup(push=not no_push, output_dir=output_dir)
        except BackupError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Created encrypted backup {result['artifact_name']}")
        click.echo(f"Ciphertext SHA-256: {result['sha256_ciphertext']}")
        click.echo(f"Plaintext SHA-256:  {result['sha256_plaintext']}")
        if result.get('local_path'):
            click.echo(f"Local artifact:     {result['local_path']}")
        if result.get('local_retained_path'):
            click.echo(f"Retained copy:      {result['local_retained_path']}")
        if no_push:
            click.echo('Skipped git push (--no-push).')
        elif result.get('pushed'):
            click.echo(f"Pushed to backup repo path: {result.get('publish', {}).get('artifact')}")
        else:
            click.echo(f"Git publish result: {result.get('publish')}")

    @app.cli.command('restore-backup')
    @click.option(
        '--file',
        'backup_file',
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help='Local .mbak file to restore.',
    )
    @click.option(
        '--artifact',
        default=None,
        help='Artifact filename in the private backup repo (default: latest). Used when --file is omitted.',
    )
    @click.option(
        '--apply',
        is_flag=True,
        help='Write restored files into the live data paths. Without this flag, only decrypt/verify.',
    )
    @click.option('--skip-database', is_flag=True, help='Do not restore the database dump.')
    @click.option('--skip-private', is_flag=True, help='Do not restore private_uploads.')
    @click.option('--skip-public', is_flag=True, help='Do not restore public vehicle/CarFax uploads.')
    @click.option('--skip-stock', is_flag=True, help='Do not restore Coming Soon stock images.')
    @with_appcontext
    def restore_backup_command(backup_file, artifact, apply, skip_database, skip_private, skip_public, skip_stock):
        """Decrypt and verify a backup; with --apply, restore data onto this server."""
        try:
            if backup_file is None:
                click.echo('Fetching encrypted backup from the private repository…')
                backup_file = fetch_remote_backup(artifact=artifact)
                click.echo(f'Using {backup_file}')
            summary = restore_backup(
                backup_file,
                apply=apply,
                restore_database=not skip_database,
                restore_private=not skip_private,
                restore_public=not skip_public,
                restore_stock=not skip_stock,
            )
        except BackupError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Plaintext SHA-256 verified: {summary['plaintext_sha256']}")
        if summary.get('manifest'):
            created = summary['manifest'].get('created_at')
            click.echo(f'Manifest created_at: {created}')
        for section, info in (summary.get('restored') or {}).items():
            click.echo(f'{section}: {json.dumps(info, sort_keys=True)}')
        if not apply:
            click.echo('Dry run only. Re-run with --apply to write files (stop the app first in production).')
        else:
            click.echo('Restore applied. Restart the application and confirm /healthz.')
