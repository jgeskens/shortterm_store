# Generated by Django 3.2.8 on 2021-10-12 21:51

from django.db import migrations, models
import store.utils
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Item',
            fields=[
                ('guid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('shortcut', models.CharField(default=store.utils.shortuuid, editable=False, max_length=16)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('modified', models.DateTimeField(auto_now=True)),
                ('text', models.TextField(blank=True)),
            ],
        ),
    ]
