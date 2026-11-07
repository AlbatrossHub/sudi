# -*- coding: utf-8 -*-
"""Sending a push notification through Firebase Cloud Messaging (HTTP v1).

The credential is a Google service-account JSON kept **on disk**, at the path named by
the system parameter ``sudi_sync.fcm_credentials_file`` — never in the database,
so a dump of the database (and the training refresh that restores one) carries no key.
No file configured, or no file at the path, means pushes are quietly off: every caller
in this module treats "cannot send" as a logging matter, because a notification is a
courtesy and must never break the business write that triggered it.

No Google SDK. The v1 API is two HTTPS calls — sign a JWT with the service account's
key, trade it for an hour's access token, POST one message per device — and the
``jwt`` library this module already requires does the signing. The access token is
cached per credential file for its lifetime.
"""
import json
import logging
import threading
import time

import jwt
import requests

_logger = logging.getLogger(__name__)

PARAM_FILE = 'sudi_sync.fcm_credentials_file'
_SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'
_TIMEOUT = 10  # seconds per HTTP call; a push is not worth a hung worker

# One access token per credential file, shared across threads of this process.
_lock = threading.Lock()
_tokens: dict[str, tuple[str, float]] = {}

# Answers from FCM that mean "this registration token is gone" — the app was
# uninstalled, or Firebase rotated the token away. The row should be deleted,
# not retried.
_DEAD_ERRORS = {'UNREGISTERED', 'INVALID_ARGUMENT', 'NOT_FOUND'}


def credentials(env):
    """The parsed service-account JSON, or ``None`` when pushes are not set up."""
    path = env['ir.config_parameter'].sudo().get_str(PARAM_FILE)
    if not path:
        return None
    try:
        with open(path, 'rb') as handle:
            creds = json.load(handle)
    except (OSError, ValueError) as error:
        _logger.warning('FCM credentials file %s is unreadable: %s', path, error)
        return None
    if not creds.get('project_id') or not creds.get('private_key') \
            or not creds.get('client_email'):
        _logger.warning('FCM credentials file %s is not a service-account key.', path)
        return None
    return creds


def _access_token(creds):
    """An OAuth2 access token for the messaging scope, cached until near expiry."""
    cache_key = creds['client_email']
    with _lock:
        cached = _tokens.get(cache_key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
    now = int(time.time())
    assertion = jwt.encode(
        {
            'iss': creds['client_email'],
            'scope': _SCOPE,
            'aud': creds.get('token_uri', 'https://oauth2.googleapis.com/token'),
            'iat': now,
            'exp': now + 3600,
        },
        creds['private_key'],
        algorithm='RS256',
    )
    response = requests.post(
        creds.get('token_uri', 'https://oauth2.googleapis.com/token'),
        data={
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': assertion,
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload['access_token']
    with _lock:
        _tokens[cache_key] = (token, now + int(payload.get('expires_in', 3600)))
    return token


def _message(device_token, title, body, data):
    """One FCM v1 message. ``data`` values are strings by protocol."""
    return {
        'message': {
            'token': device_token,
            'notification': {'title': title, 'body': body},
            'data': {key: str(value) for key, value in (data or {}).items()},
            'android': {
                'priority': 'HIGH',
                'notification': {'channel_id': 'sudi_updates'},
            },
            'apns': {
                'payload': {'aps': {'sound': 'default'}},
            },
        }
    }


def send(creds, device_tokens, title, body, data=None):
    """Deliver one notification to each token. Returns the set of dead tokens.

    Network trouble is logged and swallowed: the caller runs after a commit, and
    there is nothing useful a raised exception could reach.
    """
    dead = set()
    try:
        bearer = _access_token(creds)
    except Exception:
        _logger.warning('FCM: could not obtain an access token.', exc_info=True)
        return dead
    url = 'https://fcm.googleapis.com/v1/projects/%s/messages:send' % creds['project_id']
    sent = 0
    for device_token in device_tokens:
        try:
            response = requests.post(
                url,
                json=_message(device_token, title, body, data),
                headers={'Authorization': 'Bearer %s' % bearer},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as error:
            _logger.warning('FCM: send failed: %s', error)
            continue
        if response.ok:
            sent += 1
            continue
        # 404 UNREGISTERED / 400 INVALID_ARGUMENT: the token is dead, drop the row.
        detail = ''
        try:
            detail = response.json().get('error', {}).get('status', '')
        except ValueError:
            pass
        if detail in _DEAD_ERRORS:
            dead.add(device_token)
        else:
            _logger.warning('FCM: %s answered %s %s',
                            url, response.status_code, response.text[:400])
    _logger.info('FCM: delivered %s of %s notification(s), %s dead token(s).',
                 sent, len(device_tokens), len(dead))
    return dead


def deliver(dbname, creds, device_tokens, title, body, data=None):
    """The post-commit entry point: send, then prune dead tokens with a fresh cursor.

    Runs after the triggering transaction committed, so the cursor it rode in on is
    gone — pruning opens its own.
    """
    try:
        dead = send(creds, device_tokens, title, body, data)
        if dead:
            import odoo
            from odoo.api import Environment
            with odoo.registry(dbname).cursor() as cr:
                env = Environment(cr, odoo.SUPERUSER_ID, {})
                # The device row is the user's phone book entry; only the dead
                # token goes, the row stays.
                env['sudi.api.device'].search(
                    [('push_token', 'in', list(dead))]).write({'push_token': False})
    except Exception:
        _logger.exception('FCM: notification delivery failed.')
