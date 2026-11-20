import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urljoin

import httpx

from core.config import settings
from services.crm.bulk_import_service import CRMImportError

logger = logging.getLogger(__name__)
_sessions: dict[str, tuple[dict, datetime]] = {}
_lock = Lock()


async def authenticate(user_code: str, password: str, connection_name: str) -> dict:
    url = urljoin(settings.ERP_LOGIN_URL.rstrip('/') + '/', settings.ERP_AUTH_PATH.lstrip('/'))
    try:
        async with httpx.AsyncClient(timeout=settings.CRM_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json={
                'UserCode': user_code, 'Password': password,
                'ConnectionName': connection_name, 'PushNotificationToken': '',
            })
        envelope = response.json()
        body = envelope.get('Body')
        login = json.loads(body) if isinstance(body, str) else body
        if response.is_error or envelope.get('Status') != 200 or not isinstance(login, dict):
            raise ValueError('authentication rejected')
        if not login.get('UserId') or not login.get('DatabaseName') or not login.get('BaseUri'):
            raise ValueError('authentication response missing required login details')
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        logger.warning('GoodBooks authentication failed for user %s: %s', user_code, type(exc).__name__)
        raise CRMImportError('GoodBooks login failed. Check your user code, password, and database.', 401) from exc

    session_id = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
    with _lock:
        _sessions[session_id] = (login, expiry)
    logger.info('GoodBooks login succeeded for user %s', login.get('UserCode', user_code))
    return {'session_id': session_id, 'user_name': login.get('UserName') or user_code,
            'database_name': login['DatabaseName']}


def login_for_session(session_id: str | None) -> dict:
    with _lock:
        session = _sessions.get(session_id or '')
        if session and session[1] <= datetime.now(timezone.utc):
            _sessions.pop(session_id or '', None)
            session = None
    if not session:
        raise CRMImportError('Connect to GoodBooks before uploading to CRM.', 401)
    return session[0]


def logout(session_id: str | None) -> None:
    with _lock:
        _sessions.pop(session_id or '', None)
