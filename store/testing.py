import tempfile
from pathlib import Path

from django.test import override_settings


class TempStorageMixin:
    """Point STORAGE_ROOT at a fresh temporary directory for each test."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage_root = Path(tmp.name)
        (self.storage_root / '.tmp').mkdir()
        override = override_settings(
            STORAGE_ROOT=self.storage_root,
            FILE_UPLOAD_TEMP_DIR=str(self.storage_root / '.tmp'),
        )
        override.enable()
        self.addCleanup(override.disable)
