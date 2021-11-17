import json
from datetime import timedelta

import boto3
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from django.conf import settings

from . import models
from . import s3


def item_detail(request, item_shortcut=''):
    if len(item_shortcut) == 0:
        item = models.Item.objects.create()
        return redirect(f'/{item.guid}/')
    elif len(item_shortcut) == 8:
        item = get_object_or_404(models.Item, shortcut=item_shortcut, created__gte=now() - timedelta(minutes=5))
        return redirect(f'/{item.guid}/')
    elif len(item_shortcut) == 36:
        item = get_object_or_404(models.Item, guid=item_shortcut)

        if request.method == 'POST':
            data = json.loads(request.body.decode('utf-8'))
            if 'text' in data:
                item.text = data['text']
                item.save()
            if 'name' in data:
                item.name = data['name']
                item.save()
            return HttpResponse('OK')

        if 'json' in request.GET:
            return JsonResponse({
                'name': item.name,
                'display_name': item.display_name,
                'text': item.text,
                'uploads': [
                    {
                        'guid': str(u.guid),
                        'filename': u.key.rsplit('/', 1)[-1],
                        # 'url': u.presigned_url,
                        'url': reverse('upload_download', args=(str(item.guid), str(u.guid))),
                        'is_image': u.key.rsplit('.', 1)[-1].lower() in ('jpg', 'jpeg', 'png', 'gif')
                    }
                    for u in item.uploads.all()
                ]
            })

        return render(request, 'store/item_detail_fancy.html', {'item': item, 'now': now})
    else:
        raise Http404


@require_POST
def generate_upload_params(request, item_guid):
    filename = request.POST.get('filename')
    if not filename:
        raise Http404
    item = get_object_or_404(models.Item, guid=item_guid)
    object_path = f'{item.guid}/{filename}'
    response = s3.create_presigned_post_with_config(object_name=object_path)
    return JsonResponse(response)


@require_POST
def upload(request, item_guid):
    item = get_object_or_404(models.Item, guid=item_guid)
    data = json.loads(request.body.decode('utf-8'))
    assert 'url' in data and 'key' in data
    models.Upload.objects.get_or_create(item=item, **data)
    return HttpResponse('OK')


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
