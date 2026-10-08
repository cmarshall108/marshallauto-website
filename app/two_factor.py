import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import timedelta

import click
import pyotp
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app, session
from flask.cli import with_appcontext
from sqlalchemy import case, or_

from app import db
from app.models import AdminRecoveryCode, SiteSetting, User, utcnow


def _cipher():
    key = current_app.config['SECRET_KEY']
    raw = key.encode('utf-8') if isinstance(key, str) else key
    derived = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=b'marshallauto-admin-totp-v1',
    ).derive(raw)
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(secret):
    return _cipher().encrypt(secret.encode('ascii')).decode('ascii')


def decrypt_secret(encrypted):
    return _cipher().decrypt(encrypted.encode('ascii')).decode('ascii')


def requires_two_factor(user):
    # Read the policy from the database so changes reach every worker immediately.
    required = db.session.query(SiteSetting.value).filter_by(
        key='admin_two_factor_required',
    ).scalar()
    return (
        current_app.config['ADMIN_TWO_FACTOR_REQUIRED'] or required == 'true'
    )


def session_verified(user):
    if not requires_two_factor(user):
        return True
    return (
        user.totp_enabled and bool(user.auth_version)
        and session.get('admin_2fa_version') == user.auth_version
        and 0 <= time.time() - session.get('admin_2fa_at', 0)
        < current_app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()
    )


def start_challenge(user, next_page=None):
    csrf_token = session.get('csrf_token')
    session.clear()
    if csrf_token:
        session['csrf_token'] = csrf_token
    session['_remember'] = 'clear'
    session['admin_2fa_pending'] = {
        'user_id': user.id,
        'issued_at': time.time(),
        'password_stamp': hashlib.sha256(user.password_hash.encode()).hexdigest(),
        'auth_version': user.auth_version,
        'next': next_page,
    }


def pending_user():
    pending = session.get('admin_2fa_pending')
    if not pending:
        return None
    age = time.time() - pending.get('issued_at', 0)
    if not 0 <= age < current_app.config['TWO_FACTOR_CHALLENGE_SECONDS']:
        session.pop('admin_2fa_pending', None)
        return None
    user = db.session.get(User, pending.get('user_id'))
    if (
        not user or not user.is_active or user.is_locked
        or user.auth_version != pending.get('auth_version')
        or not hmac.compare_digest(
            hashlib.sha256(user.password_hash.encode()).hexdigest(),
            pending.get('password_stamp', ''),
        )
    ):
        session.pop('admin_2fa_pending', None)
        return None
    return user


def mark_session_verified(user):
    session['admin_2fa_version'] = user.auth_version
    session['admin_2fa_at'] = time.time()


def record_failure(user):
    count = User.failed_login_attempts + 1
    User.query.filter_by(id=user.id).update({
        User.failed_login_attempts: count,
        User.locked_until: case(
            (count >= current_app.config['LOGIN_LOCKOUT_THRESHOLD'],
             utcnow() + timedelta(minutes=current_app.config['LOGIN_LOCKOUT_MINUTES'])),
            else_=User.locked_until,
        ),
    }, synchronize_session=False)
    db.session.commit()
    db.session.refresh(user)
    current_app.logger.warning('Failed admin authentication for user %s', user.id)


def totp_counter(secret, code):
    if not re.fullmatch(r'[0-9]{6}', code):
        return None
    counter = int(time.time()) // 30
    totp = pyotp.TOTP(secret)
    for candidate in (counter, counter - 1, counter + 1):
        if hmac.compare_digest(totp.at(candidate * 30), code):
            return candidate
    return None


def _eligible_user(user):
    return User.query.filter(
        User.id == user.id, User.auth_version == user.auth_version,
        User.password_hash == user.password_hash, User.is_active_user.is_(True),
        or_(User.locked_until.is_(None), User.locked_until <= utcnow()),
    )


def consume_code(user, raw_code, enrollment=False):
    code = raw_code.strip()
    if user.totp_secret_encrypted:
        counter = totp_counter(decrypt_secret(user.totp_secret_encrypted), code)
        if counter is not None:
            query = _eligible_user(user).filter(
                User.totp_enabled.is_(not enrollment),
                User.totp_secret_encrypted == user.totp_secret_encrypted,
                or_(User.totp_last_counter.is_(None), User.totp_last_counter < counter),
            )
            return query.update({
                User.totp_last_counter: counter,
                User.totp_enabled: True,
            }, synchronize_session=False) == 1
    if enrollment or not user.totp_enabled:
        return False
    normalized = code.replace('-', '').replace(' ', '').lower()
    if not re.fullmatch(r'[0-9a-f]{20}', normalized):
        return False
    digest = hashlib.sha256(normalized.encode('ascii')).hexdigest()
    return AdminRecoveryCode.query.filter(
        AdminRecoveryCode.user_id == user.id, AdminRecoveryCode.code_hash == digest,
        _eligible_user(user).filter(User.totp_enabled.is_(True)).exists(),
    ).delete(synchronize_session=False) == 1


def replace_recovery_codes(user):
    AdminRecoveryCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    codes = [secrets.token_hex(10) for _ in range(10)]
    for code in codes:
        db.session.add(AdminRecoveryCode(
            user_id=user.id, code_hash=hashlib.sha256(code.encode('ascii')).hexdigest(),
        ))
    user.auth_version = secrets.token_hex(16)
    return ['-'.join(code[i:i + 4] for i in range(0, 20, 4)).upper() for code in codes]


def provisioning_uri(user):
    return pyotp.TOTP(decrypt_secret(user.totp_secret_encrypted)).provisioning_uri(
        name=user.username, issuer_name='Marshall Auto Admin',
    )


def register_commands(app):
    @app.cli.command('reset-admin-2fa')
    @click.argument('username')
    @with_appcontext
    def reset_admin_two_factor(username):
        """Server-owner recovery: invalidate sessions and require fresh enrollment."""
        user = User.query.filter_by(username=username).first()
        if not user:
            raise click.ClickException('Admin account not found.')
        click.confirm(
            'Reset 2FA and invalidate all sessions for this admin? Verify their identity first.',
            abort=True,
        )
        user.totp_enabled = False
        user.totp_secret_encrypted = None
        user.totp_last_counter = None
        user.auth_version = secrets.token_hex(16)
        user.failed_login_attempts = 0
        user.locked_until = None
        AdminRecoveryCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        db.session.commit()
        current_app.logger.warning('Server-owner reset of admin 2FA for user %s', user.id)
        click.echo('2FA reset. This admin must enter their password and enroll again.')
