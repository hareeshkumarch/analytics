import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path

from app.adapters.storage.local_fs import LocalArtifactStore
from app.domain.errors import SecurityPolicyError


class TestLocalArtifactStore(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "artifacts"
        self.store = LocalArtifactStore(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_publish_streams_with_correct_digest_and_leaves_no_temp_files(self) -> None:
        data = os.urandom(3 * 1024 * 1024 + 17)
        ref = self.store.stage_bytes("job1", "table.parquet", data, "application/octet-stream")
        stored = self.store.publish_staged("job1", ref, "snap_1")
        self.assertEqual((stored.sha256, stored.byte_size), (hashlib.sha256(data).hexdigest(), len(data)))
        self.assertEqual(self.store.read_bytes("snap_1"), data)
        self.assertEqual([p.name for p in (self.root / "published").iterdir()], ["snap_1"])

    def test_directories_are_private(self) -> None:
        for sub in ("", "staging", "published"):
            self.assertEqual(stat.S_IMODE((self.root / sub).stat().st_mode), 0o700)

    def test_symlink_out_of_published_is_refused(self) -> None:
        outside = Path(self._td.name) / "secret.txt"
        outside.write_text("secret")
        (self.root / "published" / "evil").symlink_to(outside)
        with self.assertRaises(SecurityPolicyError):
            self.store.read_bytes("evil")
        self.assertFalse(self.store.exists("evil"))


if __name__ == "__main__":
    unittest.main()
