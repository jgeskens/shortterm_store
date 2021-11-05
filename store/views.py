import json
from datetime import timedelta

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.timezone import now

from . import models


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
            return HttpResponse('OK')

        if 'json' in request.GET:
            return JsonResponse({'text': item.text})

        return render(request, 'store/item_detail_fancy.html', {'item': item, 'now': now})
    else:
        raise Http404
