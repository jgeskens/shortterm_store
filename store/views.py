import json
from datetime import timedelta

import uuid
from hashlib import sha1, sha256


def get_item_password_token(item):
    if not item.password:
        return ''
    return sha256((item.password + settings.SECRET_KEY).encode('utf-8')).hexdigest()
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils.timesince import timeuntil
from django.utils.http import content_disposition_header
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.core import signing
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password

from . import models
from . import storage
from . import utils

UPLOAD_TOKEN_SALT = 'store.upload'
# Checked when the upload has fully arrived, so it must cover multi-GB uploads on slow lines.
UPLOAD_TOKEN_MAX_AGE = 24 * 3600


def check_item_password(item, request):
    if not item.password:
        return True
    
    password = request.headers.get('X-Item-Password')
    if not password and request.method == 'POST':
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


def create_item_from_uuid(item_uuid: str, commit: bool = False) -> models.Item:
    item = models.Item(
        guid=uuid.UUID(item_uuid),
        shortcut=sha1(item_uuid.encode('ascii')).hexdigest()[:8]
    )
    if commit:
        item.save()
    return item


def get_or_create_item(item_uuid: str) -> models.Item:
    try:
        return models.Item.objects.get(guid=item_uuid)
    except models.Item.DoesNotExist:
        try:
            return create_item_from_uuid(item_uuid, commit=True)
        except ValueError:
            raise Http404


SHORTCUT_LIFETIME = timedelta(minutes=5)


def find_live_item(shortcut: str):
    """The item a short code points to, if it was created recently enough."""
    return (models.Item.objects
            .filter(shortcut=shortcut, created__gte=now() - SHORTCUT_LIFETIME)
            .order_by('-created')
            .first())


def parse_store_code(raw: str) -> str:
    """Turn what someone typed or pasted into the code box into a bare code."""
    value = raw.strip().lower().split('?', 1)[0].split('#', 1)[0]
    segments = [segment for segment in value.split('/') if segment]
    return segments[-1] if segments else ''


def item_detail(request, item_shortcut):
    if len(item_shortcut) == 8:
        item = find_live_item(item_shortcut)
        if item is None:
            raise Http404
        return redirect(f'/{item.guid}/')
    elif len(item_shortcut) == 36:
        in_db = False
        try:
            item = models.Item.objects.get(guid=item_shortcut)
            in_db = True
        except models.Item.DoesNotExist:
            try:
                item = create_item_from_uuid(item_shortcut)
            except ValueError:
                raise Http404

        if request.method == 'POST':
            data = json.loads(request.body.decode('utf-8'))
            if not check_item_password(item, request):
                return JsonResponse({'error': 'locked'}, status=403)

            if 'text' in data:
                item.text = data['text']
                item.save()
            if 'name' in data:
                item.name = data['name']
                item.save()
            if 'mode' in data:
                item.mode = data['mode']
                item.save()
            if 'password' in data:
                password = data['password']
                if password:
                    item.password = make_password(password)
                else:
                    item.password = ''
                item.save()
            return HttpResponse('OK')

        if 'json' in request.GET:
            if not check_item_password(item, request):
                return JsonResponse({
                    'has_password': True,
                    'error': 'locked',
                    'display_name': item.display_name,
                    'in_db': in_db,
                }, status=403)

            return JsonResponse({
                'in_db': in_db,
                'name': item.name,
                'display_name': item.display_name,
                'mode': item.mode,
                'shortcut': item.shortcut,
                'text': item.text,
                'has_password': bool(item.password),
                't': get_item_password_token(item),
                'password_token': get_item_password_token(item),
                'short_link_expires': timeuntil(item.shortcut_expires()) if item.shortcut_expires() > now() else None,
                'short_link_expires_at': item.shortcut_expires().isoformat() if item.shortcut_expires() > now() else None,
                'uploads': [
                    {
                        'guid': str(u.guid),
                        'filename': u.key.rsplit('/', 1)[-1],
                        'url': reverse('upload_download', args=(str(item.guid), str(u.guid))),
                        'is_image': u.key.rsplit('.', 1)[-1].lower() in ('jpg', 'jpeg', 'png', 'gif'),
                        'is_audio': u.key.rsplit('.', 1)[-1].lower() == 'mp3',
                        'modified': u.modified.isoformat()
                    }
                    for u in (item.uploads.all().order_by('-modified') if in_db else ())
                ]
            })

        if request.get_host() in settings.NEW_DESIGN_HOSTS:
            template = 'store/item_detail.html'
        else:
            template = 'store/item_detail_classic.html'
        return render(request, template, {'item': item, 'now': now})
    else:
        raise Http404


def new_store(request):
    return redirect(f'/{uuid.uuid4()}/')


def landing(request):
    if request.get_host() not in settings.NEW_DESIGN_HOSTS:
        return new_store(request)
    return render(request, 'store/landing.html', {'code': '', 'error': False})


def open_store(request):
    raw = request.GET.get('code', '')
    code = parse_store_code(raw)
    if len(code) == 8:
        item = find_live_item(code)
        if item is not None:
            return redirect(f'/{item.guid}/')
    elif len(code) == 36:
        try:
            return redirect(f'/{uuid.UUID(code)}/')
        except ValueError:
            pass
    return render(request, 'store/landing.html', {'code': raw.strip(), 'error': True}, status=404)


@require_POST
def generate_upload_params(request, item_guid):
    item = get_or_create_item(item_guid)
    if not check_item_password(item, request):
        return JsonResponse({'error': 'locked'}, status=403)
    try:
        key = storage.key_for(item.guid, request.POST.get('filename', ''))
    except storage.InvalidKey:
        return JsonResponse({'error': 'invalid filename'}, status=400)
    return JsonResponse({
        'url': reverse('upload_receive', args=(str(item.guid),)),
        'fields': {
            'key': key,
            'token': signing.dumps(key, salt=UPLOAD_TOKEN_SALT),
        },
    })


@csrf_exempt
@require_POST
def upload_receive(request, item_guid):
    # Authorized by the signed token from generate_upload_params, like an S3 presigned POST.
    key = request.POST.get('key', '')
    try:
        signed_key = signing.loads(
            request.POST.get('token', ''), salt=UPLOAD_TOKEN_SALT, max_age=UPLOAD_TOKEN_MAX_AGE
        )
    except signing.BadSignature:
        return JsonResponse({'error': 'invalid token'}, status=403)
    if signed_key != key or key.split('/', 1)[0] != item_guid:
        return JsonResponse({'error': 'invalid token'}, status=403)
    uploaded_file = request.FILES.get('file')
    if uploaded_file is None:
        return JsonResponse({'error': 'no file'}, status=400)
    storage.save(key, uploaded_file)
    return HttpResponse(status=204)


@require_POST
def upload(request, item_guid):
    item = get_or_create_item(item_guid)
    if not check_item_password(item, request):
        return JsonResponse({'error': 'locked'}, status=403)
    data = json.loads(request.body.decode('utf-8'))
    assert 'url' in data and 'key' in data
    # Never trust the client's key: it must live under this item.
    try:
        key = storage.key_for(item.guid, data['key'].rsplit('/', 1)[-1])
    except storage.InvalidKey:
        return JsonResponse({'error': 'invalid filename'}, status=400)
    u, created = models.Upload.objects.get_or_create(item=item, key=key, defaults={'url': data['url'][:255]})
    if not created:
        u.modified = now()
        u.save()
    return JsonResponse({
        'url': reverse('upload_download', args=(str(item.guid), str(u.guid)))
    })


@require_POST
def verify_password(request, item_guid):
    item = get_object_or_404(models.Item, guid=item_guid)
    data = json.loads(request.body.decode('utf-8'))
    password = data.get('password', '')
    if check_password(password, item.password):
        return JsonResponse({'correct': True})
    return JsonResponse({'correct': False}, status=403)


@require_POST
def upload_delete(request, item_guid, upload_guid):
    item = get_object_or_404(models.Item, guid=item_guid)
    if not check_item_password(item, request):
        return JsonResponse({'error': 'locked'}, status=403)
    upload = get_object_or_404(models.Upload, item=item, guid=upload_guid)
    storage.delete(upload.key)
    upload.delete()
    return HttpResponse('OK')


def upload_download(request, item_guid, upload_guid):
    item = get_object_or_404(models.Item, guid=item_guid)
    if not check_item_password(item, request):
        return HttpResponse('Locked', status=403)
    upload = get_object_or_404(models.Upload, item=item, guid=upload_guid)
    try:
        path = storage.path_for_key(upload.key)
    except storage.InvalidKey:
        raise Http404
    if not path.is_file():
        raise Http404
    # nginx serves the file from its internal /_protected/ location.
    ctype = storage.content_type(upload.key)
    inline = storage.is_inline_safe(ctype)
    response = HttpResponse(content_type=ctype if inline else 'application/octet-stream')
    response['Content-Disposition'] = content_disposition_header(not inline, path.name)
    response['X-Accel-Redirect'] = storage.accel_redirect(upload.key)
    return response
