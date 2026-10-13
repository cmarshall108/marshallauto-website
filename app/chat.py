"""First-party live chat with database-backed staff presence and transcripts."""
from datetime import timedelta
from uuid import UUID, uuid4

from flask import Blueprint, current_app, jsonify, render_template, request, session
from flask_login import current_user, login_required
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import db
from app.models import ChatConversation, ChatMessage, ChatPresence, User, utcnow
from app.spam_filter import MANUAL_CLEAR_MARKER, check_lead_spam_for_app
from app.utils import client_ip, rate_limit_exceeded

chat_bp = Blueprint('chat', __name__)
PRESENCE_SECONDS = 90
CLOSE_GRACE_SECONDS = 10


def _error(message, status=400):
    return jsonify(success=False, message=message), status


@chat_bp.before_request
def _limit_body():
    if (request.content_length or 0) > 16 * 1024:
        return _error('Chat requests must be smaller than 16 KiB.', 413)


@chat_bp.after_request
def _private(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    return response


@chat_bp.errorhandler(SQLAlchemyError)
def _database_error(exception):
    db.session.rollback()
    current_app.logger.exception('Live chat database operation failed (%s)', type(exception).__name__)
    return _error('Chat is temporarily unavailable. Please retry or call us.', 503)


def agents_online():
    return db.session.query(ChatPresence.id).join(
        User, User.id == ChatPresence.user_id,
    ).filter(
        User.is_active_user.is_(True),
        User.auth_version == ChatPresence.auth_version,
        ChatPresence.last_seen_at > utcnow() - timedelta(seconds=PRESENCE_SECONDS),
    ).first() is not None


def _visitor_conversation():
    identifier = session.get('chat_conversation_id')
    return db.session.get(ChatConversation, identifier) if identifier else None


def _uuid(value):
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _conversation_data(conversation, staff=False):
    data = {
        'id': conversation.id,
        'name': conversation.customer_name,
        'closed': conversation.closed_at is not None,
        'blocked': conversation.blocked_at is not None,
        'last_message_id': conversation.last_message_id,
        'updated_at': conversation.updated_at.isoformat() + 'Z',
    }
    if staff:
        lead = conversation.lead
        data.update(
            lead_id=conversation.lead_id,
            email=lead.email if lead else None,
            phone=lead.phone if lead else None,
            unread=conversation.last_customer_message_id > conversation.last_read_message_id,
            suspicious=conversation.is_suspicious,
        )
    return data


def _message_data(message, staff=False):
    data = {
        'id': message.id, 'sender': message.sender, 'body': message.body,
        'created_at': message.created_at.isoformat() + 'Z',
    }
    if staff:
        data.update(suspicious=message.is_suspicious, spam_score=message.spam_score,
                    spam_reasons=message.spam_reasons)
    return data


def _transcript(conversation, staff=False):
    after = request.args.get('after', type=int) if 'after' in request.args else 0
    if after is None or not 0 <= after <= 9223372036854775807:
        return _error('Invalid message cursor.')
    messages = ChatMessage.query.filter(
        ChatMessage.conversation_id == conversation.id,
        ChatMessage.id > after,
    ).order_by(ChatMessage.id).limit(201).all()
    return jsonify(
        success=True, conversation=_conversation_data(conversation, staff),
        online=agents_online(), messages=[_message_data(m, staff) for m in messages[:200]],
        has_more=len(messages) > 200,
    )


@chat_bp.route('/api/chat/status')
def status():
    conversation = _visitor_conversation()
    return jsonify(
        success=True, online=agents_online(),
        conversation=_conversation_data(conversation) if conversation else None,
    )


def start_conversation(form):
    """Called by the existing validated/rate-limited contact submission route."""
    from app.routes import _build_lead_from_form, _notify_lead

    existing = _visitor_conversation()
    if existing and existing.blocked_at:
        return _error('Live chat access has been blocked by our team. Please call us for assistance.', 403)
    if existing and existing.closed_at is None:
        return jsonify(success=True, conversation=_conversation_data(existing))
    if not agents_online():
        return _error('Our team is offline. You can send a regular message instead.', 409)
    if not (form.message.data or '').strip():
        return _error('Please enter a question to start the chat.')
    lead = _build_lead_from_form(form, source='live_chat')
    if lead.is_spam:
        db.session.add(lead)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Live chat spam quarantine failed')
            return _error('We could not save your message. Please call us.', 503)
        current_app.logger.info('Live chat lead %s quarantined as spam', lead.id)
        return _error('We could not start a live chat. Please call us for assistance.', 400)
    conversation = ChatConversation(
        id=str(uuid4()), customer_name=lead.name, created_at=utcnow(), updated_at=utcnow(),
        is_suspicious=lead.spam_score >= current_app.config['LEAD_SPAM_SUSPICIOUS_THRESHOLD'],
    )
    message = ChatMessage(
        conversation_id=conversation.id, client_nonce=str(uuid4()),
        sender='customer', body=form.message.data.strip(),
        spam_score=lead.spam_score, spam_reasons=lead.spam_reasons,
        is_suspicious=conversation.is_suspicious,
    )
    try:
        db.session.add(lead)
        db.session.flush()
        conversation.lead_id = lead.id
        db.session.add(conversation)
        db.session.flush()
        db.session.add(message)
        db.session.flush()
        conversation.last_message_id = message.id
        conversation.last_customer_message_id = message.id
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Live chat start failed')
        return _error('We could not save your chat. Please retry or call us.', 503)
    session['chat_conversation_id'] = conversation.id
    _notify_lead(lead)
    return jsonify(success=True, conversation=_conversation_data(conversation)), 201


def _send(conversation, staff=False):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error('Please send a valid chat message.')
    body = payload.get('body')
    nonce = _uuid(payload.get('client_nonce'))
    if not isinstance(body, str) or not body.strip() or len(body) > 5000 or not nonce:
        return _error('Messages need 1-5000 characters and a valid message ID.')
    sender = 'agent' if staff else 'customer'
    user_id = current_user.id if staff else None
    existing = ChatMessage.query.filter_by(
        conversation_id=conversation.id, client_nonce=nonce,
    ).first()
    if existing:
        if existing.sender != sender or existing.user_id != user_id or existing.body != body.strip():
            return _error('This message ID was already used for another message.', 409)
        return jsonify(success=True, message=_message_data(existing, staff))
    if conversation.blocked_at:
        return _error('This chat is blocked. Please call us for assistance.', 403)
    if conversation.closed_at:
        return _error('This chat has ended. Please start a new chat or leave a message.', 409)
    if not staff and rate_limit_exceeded(f'chat-message:{client_ip()}', 30, 60):
        return _error('Too many messages. Please wait a minute and try again.', 429)
    if ChatMessage.query.filter_by(conversation_id=conversation.id).count() >= 1000:
        return _error('This chat has reached its message limit. Please end it and start a new chat.', 409)
    spam = None
    if not staff:
        spam = check_lead_spam_for_app(current_app.config, message=body)
        if spam.is_spam:
            current_app.logger.info('Live chat message rejected as spam: conversation=%s score=%s',
                                    conversation.id, spam.score)
            return _error('This message was blocked as suspected spam. Please send a vehicle-related question or call us.', 400)
        repeated = ChatMessage.query.filter(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.sender == 'customer', ChatMessage.body == body.strip(),
            ChatMessage.created_at > utcnow() - timedelta(seconds=60),
        ).first()
        if repeated:
            return _error('You already sent this message. Please wait for a reply instead of repeating it.', 429)
    message = ChatMessage(
        conversation_id=conversation.id, sender=sender, user_id=user_id,
        body=body.strip(), client_nonce=nonce,
        is_suspicious=spam.is_suspicious if spam else False,
        spam_score=spam.score if spam else 0,
        spam_reasons=(spam.reason_text[:512] or None) if spam else None,
    )
    db.session.add(message)
    try:
        db.session.flush()
        # SQL expressions keep counters monotonic across simultaneous senders.
        updates = {
            ChatConversation.last_message_id: case(
                (ChatConversation.last_message_id < message.id, message.id),
                else_=ChatConversation.last_message_id,
            ),
            ChatConversation.updated_at: utcnow(),
        }
        if not staff:
            if message.is_suspicious:
                updates[ChatConversation.is_suspicious] = True
            updates[ChatConversation.last_customer_message_id] = case(
                (ChatConversation.last_customer_message_id < message.id, message.id),
                else_=ChatConversation.last_customer_message_id,
            )
        changed = ChatConversation.query.filter_by(
            id=conversation.id, closed_at=None,
        ).update(updates, synchronize_session=False)
        if not changed:
            db.session.rollback()
            return _error('This chat has ended. Your message was not sent.', 409)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = ChatMessage.query.filter_by(
            conversation_id=conversation.id, client_nonce=nonce,
        ).first()
        if existing and existing.sender == sender and existing.user_id == user_id and existing.body == body.strip():
            return jsonify(success=True, message=_message_data(existing, staff))
        raise
    return jsonify(success=True, message=_message_data(message, staff)), 201


@chat_bp.route('/api/chat/messages', methods=['GET', 'POST'])
def visitor_messages():
    conversation = _visitor_conversation()
    if not conversation:
        return _error('No chat is associated with this browser.', 404)
    if request.method == 'POST':
        return _send(conversation)
    return _transcript(conversation)


@chat_bp.route('/api/chat/end', methods=['POST'])
def visitor_end():
    conversation = _visitor_conversation()
    if not conversation:
        return _error('No chat is associated with this browser.', 404)
    conversation.closed_at = conversation.closed_at or utcnow()
    db.session.commit()
    return jsonify(success=True)


@chat_bp.route('/admin/live-chat')
@login_required
def inbox():
    return render_template('admin/live_chat.html')


@chat_bp.route('/admin/api/chat/notifications')
@login_required
def notifications():
    latest = db.session.query(func.max(ChatConversation.last_customer_message_id)).scalar() or 0
    unread = ChatConversation.query.filter(
        ChatConversation.closed_at.is_(None), ChatConversation.blocked_at.is_(None),
        ChatConversation.last_customer_message_id > ChatConversation.last_read_message_id,
    ).count()
    return jsonify(success=True, latest_customer_message_id=latest, unread=unread)


@chat_bp.route('/admin/api/chat/presence', methods=['POST'])
@login_required
def presence():
    data = request.get_json(silent=True) if request.is_json else request.form
    if not data:
        return _error('Invalid availability request.')
    identifier = _uuid(data.get('id'))
    available = data.get('available')
    closing = data.get('closing', False)
    if not identifier or not (
        type(available) is bool or isinstance(available, str) and available in ('true', 'false')
    ) or not (type(closing) is bool or closing in ('true', 'false')):
        return _error('Invalid availability request.')
    record = db.session.get(ChatPresence, identifier)
    if record and record.user_id != current_user.id:
        return _error('This inbox session belongs to another agent.', 403)
    if available in (True, 'true'):
        if not record:
            record = ChatPresence(id=identifier, user_id=current_user.id)
            db.session.add(record)
        record.auth_version = current_user.auth_version
        record.last_seen_at = utcnow()
    elif record:
        if closing in (True, 'true'):
            # Navigation renews presence on the next page before this lease expires.
            record.last_seen_at = min(
                record.last_seen_at,
                utcnow() - timedelta(seconds=PRESENCE_SECONDS - CLOSE_GRACE_SECONDS),
            )
        else:
            db.session.delete(record)
    ChatPresence.query.filter(
        ChatPresence.last_seen_at < utcnow() - timedelta(days=1),
    ).delete(synchronize_session=False)
    db.session.commit()
    return jsonify(success=True, online=agents_online())


@chat_bp.route('/admin/api/chat/conversations')
@login_required
def conversations():
    page = request.args.get('page', type=int) if 'page' in request.args else 1
    if page is None or not 1 <= page <= 10000:
        return _error('Invalid inbox page.')
    query = ChatConversation.query
    if request.args.get('closed') == 'true':
        query = query.filter(ChatConversation.closed_at.isnot(None))
    else:
        query = query.filter(ChatConversation.closed_at.is_(None))
    rows = query.order_by(ChatConversation.updated_at.desc(), ChatConversation.id).offset(
        (page - 1) * 50,
    ).limit(51).all()
    return jsonify(
        success=True, conversations=[_conversation_data(c, staff=True) for c in rows[:50]],
        has_more=len(rows) > 50,
    )


@chat_bp.route('/admin/api/chat/<uuid:identifier>/messages', methods=['GET', 'POST'])
@login_required
def staff_messages(identifier):
    conversation = db.session.get(ChatConversation, str(identifier))
    if not conversation:
        return _error('Chat not found.', 404)
    return _send(conversation, staff=True) if request.method == 'POST' else _transcript(conversation, staff=True)


@chat_bp.route('/admin/api/chat/<uuid:identifier>/read', methods=['POST'])
@login_required
def read(identifier):
    conversation = db.session.get(ChatConversation, str(identifier))
    data = request.get_json(silent=True)
    cursor = data.get('through') if isinstance(data, dict) else None
    if not conversation:
        return _error('Chat not found.', 404)
    if type(cursor) is not int or cursor < 0 or cursor > conversation.last_message_id:
        return _error('Invalid read cursor.')
    ChatConversation.query.filter(
        ChatConversation.id == conversation.id,
        ChatConversation.last_read_message_id < cursor,
    ).update({ChatConversation.last_read_message_id: cursor}, synchronize_session=False)
    db.session.commit()
    return jsonify(success=True)


@chat_bp.route('/admin/api/chat/<uuid:identifier>/end', methods=['POST'])
@login_required
def staff_end(identifier):
    conversation = db.session.get(ChatConversation, str(identifier))
    if not conversation:
        return _error('Chat not found.', 404)
    conversation.closed_at = conversation.closed_at or utcnow()
    db.session.commit()
    return jsonify(success=True)


@chat_bp.route('/admin/api/chat/<uuid:identifier>/block', methods=['POST'])
@login_required
def staff_block(identifier):
    conversation = db.session.get(ChatConversation, str(identifier))
    if not conversation:
        return _error('Chat not found.', 404)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or type(payload.get('blocked')) is not bool:
        return _error('Specify whether to block this conversation.')
    blocked = payload['blocked']
    conversation.blocked_at = utcnow() if blocked else None
    if blocked:
        conversation.closed_at = conversation.closed_at or utcnow()
    if conversation.lead:
        conversation.lead.is_spam = blocked
        conversation.lead.spam_reasons = 'Live chat blocked as spam by staff' if blocked else MANUAL_CLEAR_MARKER
        if not blocked:
            conversation.lead.spam_score = 0
    db.session.commit()
    current_app.logger.info('Live chat block updated: conversation=%s staff=%s blocked=%s',
                            conversation.id, current_user.id, blocked)
    return jsonify(success=True, conversation=_conversation_data(conversation, staff=True))
