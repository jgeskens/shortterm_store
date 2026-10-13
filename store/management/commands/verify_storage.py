from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from store import storage
from store.models import Upload


class Command(BaseCommand):
    help = 'Compare upload rows in the database with the files under STORAGE_ROOT.'

    def handle(self, *args, **options):
        root = Path(settings.STORAGE_ROOT).resolve()
        files = {}
        for path in root.rglob('*'):
            relative = path.relative_to(root)
            if path.is_file() and relative.parts[0] != '.tmp':
                files[relative.as_posix()] = path.stat().st_size

        keys = set(Upload.objects.values_list('key', flat=True))
        missing = sorted(key for key in keys if not self._exists(key))
        orphans = sorted(set(files) - keys)

        self.stdout.write(f'Files on disk: {len(files)} ({sum(files.values())} bytes)')
        self.stdout.write(f'Upload rows: {len(keys)}')
        self.stdout.write(f'Missing files: {len(missing)}')
        for key in missing:
            self.stdout.write(f'  {key}')
        self.stdout.write(f'Orphan files: {len(orphans)}')
        for key in orphans:
            self.stdout.write(f'  {key}')

        if missing:
            raise CommandError(f'{len(missing)} upload(s) have no file')

    @staticmethod
    def _exists(key):
        try:
            return storage.path_for_key(key).is_file()
        except storage.InvalidKey:
            return False
