import json
import re
from hashlib import sha256

from django.conf import settings
from django.contrib.auth.hashers import check_password

from .models import DROPBOX, READ

OWNER = 'owner'


def get_item_password_token(item):
    if not item.password:
        return ''
    return sha256((item.password + settings.SECRET_KEY).encode('utf-8')).hexdigest()


def check_item_password(item, request):
    if not item.password:
        return True

    password = request.headers.get('X-Item-Password')
    if not password and request.method == 'POST' and request.content_type == 'application/json':
        try:
            data = json.loads(request.body.decode('utf-8'))
            password = data.get('password')
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    if not password:
        password = request.GET.get('password')

    if password and check_password(password, item.password):
        return True

    password_token = request.GET.get('t')
    if password_token and password_token == get_item_password_token(item):
        return True

    return False


def uses_new_design(request):
    return request.get_host() in settings.NEW_DESIGN_HOSTS


def access_for(item, request):
    """OWNER with the password (or none set), else the item's visitor level, else None."""
    if check_item_password(item, request):
        return OWNER
    if item.visitor_access in (READ, DROPBOX) and uses_new_design(request):
        return item.visitor_access
    return None


def strip_token(text, token):
    """Remove the `t=<token>` query parameter from every URL in text."""
    if not token:
        return text
    t = re.escape(token)
    text = re.sub(rf'\?t={t}&', '?', text)
    text = re.sub(rf'[?&]t={t}(?![0-9A-Za-z])', '', text)
    return text.replace(token, '')
