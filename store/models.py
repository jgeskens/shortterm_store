import uuid
from datetime import timedelta

from django.db import models
from django.utils.timezone import now

from . import utils


class ItemManager(models.Manager):
    def cleanup(self):
        self.filter(modified__lt=now() - timedelta(hours=1)).delete()


class Item(models.Model):
    guid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shortcut = models.CharField(max_length=16, default=utils.shortuuid, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    text = models.TextField(blank=True)

    objects = ItemManager()

    def __str__(self):
        return str(self.guid)

    def shortcut_expires(self):
        return self.created + timedelta(minutes=5)
