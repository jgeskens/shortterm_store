from django.contrib import admin

from . import models


@admin.register(models.Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('guid', 'shortcut', 'created', 'modified', 'text')
    date_hierarchy = 'created'
    readonly_fields = ('guid', 'shortcut', 'created', 'modified')
