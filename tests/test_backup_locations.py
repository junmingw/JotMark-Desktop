"""Backup grouping and collision protection for external annotation outputs."""

import tempfile
import unittest
from pathlib import Path

from jotmark.data.annotation_safety import backup_previous_annotation
from jotmark.data.project_config import BACKUP_ROOT


class BackupLocationTests(unittest.TestCase):
    def test_external_outputs_with_same_filename_have_distinct_project_histories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            first = root / "first"
            second = root / "second"
            for path in (project, first, second):
                path.mkdir()
            (first / "road.json").write_text("first", encoding="utf-8")
            (second / "road.json").write_text("second", encoding="utf-8")
            backup1 = Path(backup_previous_annotation(first / "road.json", project))
            backup2 = Path(backup_previous_annotation(second / "road.json", project))
            self.assertNotEqual(backup1, backup2)
            self.assertTrue(backup1.is_relative_to(project / BACKUP_ROOT))
            self.assertTrue(backup2.is_relative_to(project / BACKUP_ROOT))
            self.assertEqual(backup1.read_text(encoding="utf-8"), "first")
            self.assertEqual(backup2.read_text(encoding="utf-8"), "second")
            self.assertFalse((first / BACKUP_ROOT).exists())
            self.assertFalse((second / BACKUP_ROOT).exists())

    def test_old_backups_are_not_moved_or_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / ".jotmark_backups" / "annotation-history" / "road.json.previous"
            old.parent.mkdir(parents=True)
            old.write_text("old backup", encoding="utf-8")
            target = root / "road.json"
            target.write_text("current", encoding="utf-8")
            new = Path(backup_previous_annotation(target))
            self.assertEqual(old.read_text(encoding="utf-8"), "old backup")
            self.assertEqual(new.read_text(encoding="utf-8"), "current")
            self.assertTrue(new.is_relative_to(root / BACKUP_ROOT))


if __name__ == "__main__":
    unittest.main()
