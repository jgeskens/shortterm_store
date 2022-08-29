import json
from datetime import timedelta

import boto3
import uuid
from hashlib import sha1
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils.timesince import timeuntil
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from django.conf import settings

from . import models
from . import s3
from . import utils


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


def item_detail(request, item_shortcut=''):
    if len(item_shortcut) == 0:
        return redirect(f'/{uuid.uuid4()}/')
    elif len(item_shortcut) == 8:
        item = get_object_or_404(models.Item, shortcut=item_shortcut, created__gte=now() - timedelta(minutes=5))
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
            if 'text' in data:
                item.text = data['text']
                item.save()
            if 'name' in data:
                item.name = data['name']
                item.save()
            if 'mode' in data:
                item.mode = data['mode']
                item.save()
            return HttpResponse('OK')

        if 'json' in request.GET:
            return JsonResponse({
                'in_db': in_db,
                'name': item.name,
                'display_name': item.display_name,
                'mode': item.mode,
                'shortcut': item.shortcut,
                'text': item.text,
                'short_link_expires': timeuntil(item.shortcut_expires()) if item.shortcut_expires() > now() else None,
                'uploads': [
                    {
                        'guid': str(u.guid),
                        'filename': u.key.rsplit('/', 1)[-1],
                        'url': reverse('upload_download', args=(str(item.guid), str(u.guid))),
                        'is_image': u.key.rsplit('.', 1)[-1].lower() in ('jpg', 'jpeg', 'png', 'gif')
                    }
                    for u in (item.uploads.all() if in_db else ())
                ]
            })

        return render(request, 'store/item_detail.html', {'item': item, 'now': now})
    else:
        raise Http404


@require_POST
def generate_upload_params(request, item_guid):
    filename = request.POST.get('filename')
    if not filename:
        raise Http404
    item = get_or_create_item(item_guid)
    object_path = f'{item.guid}/{filename}'
    response = s3.create_presigned_post_with_config(object_name=object_path)
    return JsonResponse(response)


@require_POST
def upload(request, item_guid):
    item = get_or_create_item(item_guid)
    data = json.loads(request.body.decode('utf-8'))
    assert 'url' in data and 'key' in data
    u, created = models.Upload.objects.get_or_create(item=item, **data)
    return JsonResponse({
        'url': reverse('upload_download', args=(str(item.guid), str(u.guid)))
    })


@require_POST
def upload_delete(request, item_guid, upload_guid):
    item = get_object_or_404(models.Item, guid=item_guid)
    upload = get_object_or_404(models.Upload, item=item, guid=upload_guid)
    s3_client = boto3.client('s3')
    s3_client.delete_object(Bucket=settings.UPLOAD_BUCKET, Key=upload.key)
    upload.delete()
    return HttpResponse('OK')


def upload_download(request, item_guid, upload_guid):
    item = get_object_or_404(models.Item, guid=item_guid)
    upload = get_object_or_404(models.Upload, item=item, guid=upload_guid)
    return redirect(upload.presigned_url)
