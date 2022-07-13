from .models import Item


def clean_up_empty_stores(*args, **kwargs):
    Item.objects.cleanup_empty()
