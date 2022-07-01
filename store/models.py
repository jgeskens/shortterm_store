import uuid
from datetime import timedelta

from django.db import models
from django.utils.timezone import now
from django.core.cache import cache

from . import utils


class ItemManager(models.Manager):
    def cleanup(self):
        self.filter(modified__lt=now() - timedelta(hours=1)).delete()


class Item(models.Model):
    guid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shortcut = models.CharField(max_length=16, default=utils.shortuuid, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    name = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)

    objects = ItemManager()

    def __str__(self):
        return str(self.guid)

    def shortcut_expires(self):
        return (self.created or now()) + timedelta(minutes=5)

    @property
    def display_name(self):
        return self.name or f'Store {self.guid}'


class Upload(models.Model):
    guid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='uploads')
    key = models.CharField(max_length=255)
    url = models.CharField(max_length=255)

    def __str__(self):
        return f'{self.guid}'

    @property
    def presigned_url(self):
        from . import s3
        cache_key = f'upload_presigned_url_{self.guid}'
        url = cache.get(cache_key)

        if url is None:
            url = s3.create_presigned_url_with_config(self.key)
            cache.set(cache_key, url, 300)

        return url
