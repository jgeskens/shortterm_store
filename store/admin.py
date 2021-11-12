from django.contrib import admin

from . import models


class UploadInline(admin.TabularInline):
    model = models.Upload
    extra = 0


@admin.register(models.Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('guid', 'shortcut', 'created', 'modified', 'text')
    date_hierarchy = 'created'
    readonly_fields = ('guid', 'shortcut', 'created', 'modified')
    inlines = [UploadInline]

