from django.core.management.base import BaseCommand

from store.models import Item


class Command(BaseCommand):
    help = 'Delete stores without name, text or uploads.'

    def handle(self, *args, **options):
        Item.objects.cleanup_empty()
