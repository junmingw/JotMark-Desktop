"""Project import diagnostics and non-destructive UI regression tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jotmark.data.import_diagnostics import classify_import_failure

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


class ImportErrorClassificationTests(unittest.TestCase):
    def test_windows_file_in_use_is_distinct_from_permission_denied(self):
        error = OSError("file in use")
        error.winerror = 32
        self.assertIn("占用", classify_import_failure(error, "备份原配置")[0])
        self.assertIn("权限", classify_import_failure(PermissionError("denied"), "写入项目")[0])

    def test_encoding_syntax_and_unexpected_errors_are_not_conflated(self):
        encoding = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        self.assertIn("编码", classify_import_failure(encoding, "读取源文件")[0])
        syntax = json.JSONDecodeError("expected", "{", 1)
        self.assertIn("JSON", classify_import_failure(syntax, "读取源文件")[0])
        self.assertIn("未能自动确定", classify_import_failure(Exception("unknown"), "写入项目")[0])


@unittest.skipUnless(PySide6, "PySide6 is required")
class ProjectImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.old_manual = os.environ.get("JOTMARK_MANUAL_ONLY")
        os.environ["JOTMARK_MANUAL_ONLY"] = "1"
        cls.settings = tempfile.TemporaryDirectory()
        cls.old_config = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = cls.settings.name
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        if cls.old_config is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = cls.old_config
        if cls.old_manual is None:
            os.environ.pop("JOTMARK_MANUAL_ONLY", None)
        else:
            os.environ["JOTMARK_MANUAL_ONLY"] = cls.old_manual
        cls.settings.cleanup()

    def setUp(self):
        from main import MainWindow
        from PySide6.QtGui import QImage
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "项目 路面"
        self.project.mkdir()
        self.image = self.project / "road.png"
        QImage(120, 90, QImage.Format_RGB32).save(str(self.image))
        self.win = MainWindow()
        self.win.current_dir = str(self.project)
        self.win.current_image_path = str(self.image)
        self.win.scene.load_image(str(self.image))
        self.win.attribute_store.load(str(self.project))
        self.win.add_class_to_list("old")

    def tearDown(self):
        self.win._autosave_timer.stop()
        self.win._autosave_dirty = False
        self.win.close()
        self.app.processEvents()
        self.temp.cleanup()

    def _choose_dialog_button(self, text, capture=None):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox

        def choose():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                if capture is not None:
                    capture.append((dialog.text(), dialog.informativeText(), [b.text() for b in dialog.buttons()]))
                for button in dialog.buttons():
                    if button.text() == text:
                        button.click()
                        return
                dialog.reject()
        QTimer.singleShot(20, choose)

    def _source(self, name, contents):
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        return path

    def test_attribute_import_requires_project_and_exposes_copyable_reason(self):
        self.win.current_dir = ""
        with patch("main.QMessageBox.warning") as warning:
            self.win.import_attribute_file()
        warning.assert_called_once()
        self.assertIn("未选择图片项目", warning.call_args.args[2])
        self.assertIn("未选择图片项目", self.win._last_runtime_error)
        self.assertFalse(self.win.btnErrorDetails.isHidden())

    def test_invalid_json_and_wrong_annotation_json_do_not_modify_project(self):
        target = self.project / "class_attributes.json"
        target.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        original = target.read_bytes()
        for name, contents, expected in (("broken.json", "{", "JSON 格式有误"),
                                         ("annotation.json", '{"shapes": []}', "不是 JotMark 属性配置")):
            with self.subTest(name=name):
                source = self._source(name, contents)
                with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
                     patch("main.QMessageBox.warning"):
                    self.win.import_attribute_file()
                self.assertIn(expected, self.win._last_runtime_error)
                self.assertEqual(target.read_bytes(), original)

    def test_valid_attribute_import_saves_without_changing_annotations(self):
        source = self._source("属性.json", json.dumps({"global": [{"id": "visibility", "name": "visibility"}],
                                                        "classes": {"old": [{"id": "kind", "name": "kind"}]}}))
        annotation = self.project / "road.json"
        annotation.write_text('{"shapes": [{"attributes": {"other": "preserve"}}]}', encoding="utf-8")
        original = annotation.read_bytes()
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_attribute_file()
        saved = json.loads((self.project / "class_attributes.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["global"][0]["id"], "visibility")
        self.assertEqual(saved["classes"]["old"][0]["id"], "kind")
        self.assertEqual(annotation.read_bytes(), original)

    def test_first_attribute_import_initializes_missing_store_without_backup_or_prompt(self):
        from jotmark.data.attribute_schema import ClassAttributeStore
        self.win.attribute_store = ClassAttributeStore()
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.warning") as warning, \
             patch("main.QMessageBox.exec", side_effect=AssertionError("unexpected confirmation")):
            self.win.import_attribute_file()
        warning.assert_not_called()
        saved = json.loads((self.project / "class_attributes.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["global"][0]["name"], "G")
        self.assertEqual(self.win.attribute_store.directory, str(self.project))
        self.assertEqual(list(self.project.glob("JotMark备份/属性导入_*")), [])

    def test_attribute_import_accepts_common_windows_json_encodings(self):
        for encoding in ("utf-8-sig", "utf-16", "utf-32", "gb18030"):
            with self.subTest(encoding=encoding):
                source = self.root / f"schema-{encoding}.json"
                source.write_bytes('{"global": [{"name": "颜色"}], "classes": {}}'.encode(encoding))
                with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
                     patch("main.DialogOver"), patch("main.QMessageBox.information"), patch("main.QMessageBox.warning") as warning:
                    self.win.import_attribute_file()
                warning.assert_not_called()
                self.assertEqual(self.win.attribute_store.global_attributes()[0]["name"], "颜色")
                from jotmark.data.attribute_schema import ClassAttributeStore
                reopened = ClassAttributeStore()
                reopened.load(str(self.project))
                self.assertIsNone(reopened.load_error)
                self.assertEqual(reopened.global_attributes()[0]["name"], "颜色")

    def test_project_schema_with_utf8_bom_loads_without_false_corruption(self):
        path = self.project / "class_attributes.json"
        path.write_bytes('{"global": [{"name": "可见性"}], "classes": {}}'.encode("utf-8-sig"))
        self.win.attribute_store.load(str(self.project))
        self.assertIsNone(self.win.attribute_store.load_error)
        self.assertEqual(self.win.attribute_store.global_attributes()[0]["name"], "可见性")

    def test_windows_sharing_violation_is_retried_before_success(self):
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        original_save = self.win.attribute_store.save
        error = OSError("file busy")
        error.winerror = 32
        attempts = []

        def flaky_save():
            attempts.append(True)
            if len(attempts) == 1:
                raise error
            return original_save()

        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.warning") as warning, \
             patch.object(self.win.attribute_store, "save", side_effect=flaky_save), patch("main.time.sleep"):
            self.win.import_attribute_file()
        warning.assert_not_called()
        self.assertEqual(len(attempts), 2)
        self.assertTrue((self.project / "class_attributes.json").exists())

    def test_persistent_windows_file_lock_reports_error_without_writing(self):
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        error = OSError("file busy")
        error.winerror = 32
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.read_attribute_json", side_effect=error) as read, \
             patch("main.QMessageBox.warning"), patch("main.time.sleep"):
            self.win.import_attribute_file()
        self.assertEqual(read.call_count, 3)
        self.assertIn("占用", self.win._last_runtime_error)
        self.assertFalse((self.project / "class_attributes.json").exists())

    def test_importing_the_current_schema_does_not_rewrite_or_backup(self):
        target = self.project / "class_attributes.json"
        original = '{"global": [{"id": "g", "name": "G"}], "classes": {}}'
        target.write_text(original, encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(target), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.information") as info, patch("main.QMessageBox.warning") as warning:
            self.win.import_attribute_file()
        warning.assert_not_called()
        info.assert_called_once()
        self.assertIn("未替换或备份", info.call_args.args[2])
        self.assertEqual(target.read_text(encoding="utf-8"), original)
        self.assertFalse((self.project / "JotMark备份").exists())

    def test_identical_attribute_import_shows_real_information_dialog(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        target = self.project / "class_attributes.json"
        target.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        observed = []

        def close_dialog():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                observed.append((dialog.windowTitle(), dialog.text()))
                dialog.accept()

        QTimer.singleShot(30, close_dialog)
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(target), "")):
            self.win.import_attribute_file()
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0], "属性未变化")
        self.assertIn("未替换或备份", observed[0][1])

    def test_equivalent_idless_template_preserves_ids_metadata_and_original_file(self):
        target = self.project / "class_attributes.json"
        existing = {"version": 2, "global": [{"id": "parent", "name": "颜色", "type": "single_select",
                    "options": ["红", "蓝"], "children": [{"id": "child", "name": "深浅", "type": "text", "options": [], "children": []}]}],
                    "classes": {}, "note": "keep"}
        target.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        original = target.read_bytes()
        self.win.attribute_store.load(str(self.project))
        template = {"version": 2, "global": [{"name": "颜色", "options": ["红", "蓝"],
                     "children": [{"name": "深浅", "type": "text"}]}], "classes": {}, "note": "keep"}
        source = self._source("template.json", json.dumps(template, ensure_ascii=False))
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.information") as info, patch.object(self.win, "_backup_category_operation", side_effect=AssertionError("unexpected backup")), \
             patch.object(self.win.attribute_store, "save", side_effect=AssertionError("unexpected write")):
            self.win.import_attribute_file()
        info.assert_called_once()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.win.attribute_store.global_attributes()[0]["children"][0]["id"], "child")

    def test_changed_attribute_id_or_metadata_is_not_a_noop(self):
        existing = {"version": 2, "global": [{"id": "a", "name": "kind", "type": "single_select", "options": ["x"], "children": []}], "classes": {}, "note": "old"}
        target = self.project / "class_attributes.json"
        for change in ("id", "metadata", "metadata_type"):
            with self.subTest(change=change):
                target.write_text(json.dumps(existing), encoding="utf-8")
                self.win.attribute_store.load(str(self.project))
                incoming = json.loads(json.dumps(existing))
                if change == "id":
                    incoming["global"][0]["id"] = "different"
                elif change == "metadata":
                    incoming["note"] = "new"
                else:
                    existing["typed"] = True
                    target.write_text(json.dumps(existing), encoding="utf-8")
                    self.win.attribute_store.load(str(self.project))
                    incoming["typed"] = 1
                source = self._source("changed.json", json.dumps(incoming))
                with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
                    self.win.import_attribute_file()
                backup = Path(self.win._last_attribute_import_backup)
                self.assertEqual(json.loads((backup / "project" / target.name).read_text(encoding="utf-8")), existing)
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), incoming)

    def test_external_equal_schema_is_reloaded_without_backup(self):
        target = self.project / "class_attributes.json"
        target.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        new = '{"global": [{"id": "g", "name": "G"}], "classes": {}}'
        target.write_text(new, encoding="utf-8")
        source = self._source("new.json", new)
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.information"), patch.object(self.win, "_backup_category_operation", side_effect=AssertionError("unexpected backup")):
            self.win.import_attribute_file()
        self.assertEqual(target.read_text(encoding="utf-8"), new)
        self.assertEqual(self.win.attribute_store.global_attributes()[0]["id"], "g")

    def test_attribute_change_during_comparison_cannot_be_reported_as_equal(self):
        target = self.project / "class_attributes.json"
        original = '{"global": [], "classes": {}}'
        target.write_text(original, encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        source = self._source("same.json", original)
        changed = '{"global": [], "classes": {}, "outside": true}'

        def concurrent_change(_raw):
            target.write_text(changed, encoding="utf-8")
            return True

        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.QMessageBox.warning"), patch.object(self.win.attribute_store, "matches_import", side_effect=concurrent_change):
            self.win.import_attribute_file()
        self.assertEqual(target.read_text(encoding="utf-8"), changed)
        self.assertIn("比较期间", self.win._last_runtime_error)
        self.assertFalse(list(self.project.glob("JotMark备份/属性导入_*")))

    def test_windows_sharing_violation_during_backup_is_retried(self):
        import shutil
        target = self.project / "class_attributes.json"
        target.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        original_copy = shutil.copy2
        error = OSError("file busy")
        error.winerror = 32
        attempts = []

        def flaky_copy(src, dst):
            attempts.append(True)
            if len(attempts) == 1:
                raise error
            return original_copy(src, dst)

        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.warning") as warning, \
             patch("main.shutil.copy2", side_effect=flaky_copy), patch("main.time.sleep"):
            self.win.import_attribute_file()
        warning.assert_not_called()
        self.assertGreaterEqual(len(attempts), 2)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["global"][0]["name"], "G")
        backup = Path(self.win._last_attribute_import_backup) / "project" / target.name
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["global"], [])

    def test_attribute_import_backups_project_and_separate_output(self):
        self.win.save_classes()
        target = self.project / "class_attributes.json"
        target.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        output = self.root / "标注输出"
        output.mkdir()
        annotation = output / "road.json"
        annotation.write_text('{"shapes": []}', encoding="utf-8")
        self.win.annotation_output_dir = str(output)
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_attribute_file()
        backup = Path(self.win._last_attribute_import_backup)
        self.assertEqual((backup / "project" / "classes.txt").read_text(encoding="utf-8"), "old")
        self.assertEqual((backup / "project" / "class_attributes.json").read_text(encoding="utf-8"), '{"global": [], "classes": {}}')
        self.assertEqual((backup / "annotations" / "road.json").read_bytes(), annotation.read_bytes())

    def test_attribute_changed_after_backup_is_not_overwritten(self):
        target = self.project / "class_attributes.json"
        original = '{"global": [], "classes": {}}'
        target.write_text(original, encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        external = '{"global": [], "classes": {}, "external": true}'
        original_save = self.win.attribute_store.save

        def concurrent_save():
            target.write_text(external, encoding="utf-8")
            return original_save()

        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.QMessageBox.warning"), patch.object(self.win.attribute_store, "save", side_effect=concurrent_save):
            self.win.import_attribute_file()
        self.assertEqual(target.read_text(encoding="utf-8"), external)
        self.assertIn("目标文件受到保护", self.win._last_runtime_error)
        backups = list(self.project.glob("JotMark备份/属性导入_*/project/class_attributes.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_attribute_import_recovers_existing_invalid_project_schema(self):
        target = self.project / "class_attributes.json"
        target.write_text("", encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        self.assertTrue(self.win.attribute_store.load_error)
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.warning") as warning:
            self.win.import_attribute_file()
        warning.assert_not_called()
        self.assertEqual((Path(self.win._last_attribute_import_backup) / "project" / target.name).read_bytes(), b"")
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["global"][0]["name"], "G")

    def test_unmatched_category_attributes_are_reported_after_import(self):
        source = self._source("unmatched.json", '{"global": [], "classes": {"boundary": [{"name": "kind"}]}}')
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.information") as info:
            self.win.import_attribute_file()
        info.assert_not_called()
        self.assertIn("boundary", self.win.statusBar.toolTip())

    def test_existing_schema_is_backed_up_only_for_the_first_changed_import(self):
        target = self.project / "class_attributes.json"
        first = '{"global": [], "classes": {}}'
        target.write_text(first, encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        source = self._source("schema.json", '{"global": [{"id": "g", "name": "G"}], "classes": {}}')
        for _ in range(2):
            with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
                 patch("main.DialogOver"), patch("main.QMessageBox.information"), patch("main.QMessageBox.exec", side_effect=AssertionError("unexpected confirmation")):
                self.win.import_attribute_file()
        backups = sorted(self.project.glob("JotMark备份/属性导入_*/project/class_attributes.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn(first, [path.read_text(encoding="utf-8") for path in backups])
        self.assertRegex(backups[0].parent.parent.name, r"^属性导入_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_")

    def test_failed_write_keeps_original_and_reports_backup(self):
        target = self.project / "class_attributes.json"
        target.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        original = target.read_bytes()
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.QMessageBox.warning"), patch.object(self.win.attribute_store, "save", side_effect=PermissionError("denied")):
            self.win.import_attribute_file()
        self.assertEqual(target.read_bytes(), original)
        self.assertIn("没有所需权限", self.win._last_runtime_error)
        self.assertIn("备份路径", self.win._last_runtime_error)
        self.assertEqual(self.win.attribute_store.global_attributes(), [])

    def test_external_schema_change_is_backed_up_then_replaced(self):
        target = self.project / "class_attributes.json"
        target.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        external = '{"global": [], "classes": {}, "external": true}'
        target.write_text(external, encoding="utf-8")
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.QMessageBox.warning") as warning, patch("main.DialogOver"):
            self.win.import_attribute_file()
        warning.assert_not_called()
        backup = Path(self.win._last_attribute_import_backup) / "project" / "class_attributes.json"
        self.assertEqual(backup.read_text(encoding="utf-8"), external)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["global"][0]["name"], "G")

    def test_backup_failure_aborts_before_writing(self):
        target = self.project / "class_attributes.json"
        original = '{"global": [], "classes": {}}'
        target.write_text(original, encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.QMessageBox.warning"), patch("main.shutil.copy2", side_effect=PermissionError("backup denied")):
            self.win.import_attribute_file()
        self.assertEqual(target.read_text(encoding="utf-8"), original)
        self.assertIn("备份原配置", self.win._last_runtime_error)
        self.assertIn("权限", self.win._last_runtime_error)

    def test_backup_detects_change_while_copying(self):
        import shutil
        target = self.project / "class_attributes.json"
        target.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        external = '{"global": [], "classes": {}, "changed": true}'
        source = self._source("schema.json", '{"global": [{"name": "G"}], "classes": {}}')
        original_copy = shutil.copy2

        def concurrent_change(src, dst):
            result = original_copy(src, dst)
            Path(src).write_text(external, encoding="utf-8")
            return result

        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.QMessageBox.warning"), patch("main.shutil.copy2", side_effect=concurrent_change):
            self.win.import_attribute_file()
        self.assertEqual(target.read_text(encoding="utf-8"), external)
        self.assertIn("备份期间", self.win._last_runtime_error)
        self.assertEqual(self.win.attribute_store.global_attributes(), [])

    def test_existing_sidecar_still_offers_append_and_replace(self):
        sidecar = self.project / "road.json"
        sidecar.write_text("{}", encoding="utf-8")
        source = self._source("categories.txt", "new\n")
        captured = []
        self._choose_dialog_button("追加类别", captured)
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        self.assertEqual(self.win.class_list, ["old", "new"])
        self.assertIn("替换当前类别", captured[0][2])
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "{}")

    def test_same_class_order_skips_replacement_and_backup(self):
        self.win.save_classes()
        path = self.project / "classes.txt"
        original = path.read_bytes()
        source = self._source("same.txt", "# comment\nold\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.information") as info, patch.object(self.win, "_backup_category_operation", side_effect=AssertionError("unexpected backup")):
            self.win.import_class_file()
        info.assert_called_once()
        self.assertIn("未替换或备份", info.call_args.args[2])
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.project / ".jotmark_explicit_classes.json").exists())
        self.assertFalse(list(self.project.glob("JotMark备份/类别替换_*")))

    def test_identical_category_replacement_shows_real_information_dialog(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        self.win.save_classes()
        source = self._source("same.txt", "old\n")
        self._choose_dialog_button("替换当前类别")
        observed = []

        def close_info():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                observed.append((dialog.windowTitle(), dialog.text()))
                dialog.accept()

        QTimer.singleShot(100, close_info)
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            self.win.import_class_file()
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0], "类别未变化")
        self.assertIn("未替换或备份", observed[0][1])

    def test_append_existing_classes_skips_writes(self):
        self.win.save_classes()
        path = self.project / "classes.txt"
        original = path.read_bytes()
        source = self._source("same.txt", "old\n")
        self._choose_dialog_button("追加类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.information") as info, patch.object(self.win, "save_classes", side_effect=AssertionError("unexpected write")):
            self.win.import_class_file()
        info.assert_called_once()
        self.assertEqual(path.read_bytes(), original)

    def test_changed_class_order_is_backed_up_and_replaced(self):
        self.win.add_class_to_list("second")
        self.win.save_classes()
        path = self.project / "classes.txt"
        original = path.read_bytes()
        source = self._source("reordered.txt", "second\nold\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        self.assertEqual((Path(self.win._last_category_backup) / "project" / "classes.txt").read_bytes(), original)
        self.assertEqual(self.win.class_list, ["second", "old"])

    def test_current_list_equal_but_changed_disk_class_is_not_skipped(self):
        self.win.save_classes()
        path = self.project / "classes.txt"
        path.write_text("external", encoding="utf-8")
        source = self._source("same-as-ui.txt", "old\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        self.assertEqual((Path(self.win._last_category_backup) / "project" / "classes.txt").read_text(encoding="utf-8"), "external")
        self.assertEqual(path.read_text(encoding="utf-8"), "old")

    def test_replace_existing_json_backs_up_and_keeps_legacy_labels_transient(self):
        self.win.save_classes()
        schema = self.win.attribute_store
        schema.set_attributes("old", [{"id": "old_kind", "name": "kind", "type": "text"}])
        schema.save()
        categories = (self.project / "classes.txt").read_bytes()
        attrs = (self.project / "class_attributes.json").read_bytes()
        annotation = self.project / "road.json"
        annotation.write_text(json.dumps({"imagePath": "road.png", "shapes": [{"label": "old", "shape_type": "polygon",
                              "points": [[10, 10], [50, 10], [40, 50]], "attributes": {"old_kind": "value"}}]}), encoding="utf-8")
        original = annotation.read_bytes()
        self.win.load_annotations(str(self.image))
        self.win._remember_current_annotation_signature()
        shape = next(item for item in self.win.scene.items() if getattr(item, "label", "") == "old")
        shape.setSelected(True)
        self.win.update_annotation_tree()
        source = self._source("categories.txt", "new\nsecond\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.DialogOver"), patch("main.QMessageBox.warning") as warning:
            self.win.import_class_file()
        warning.assert_not_called()
        backup = Path(self.win._last_category_backup) / "project"
        self.assertEqual(backup.parent.parent, self.project / "JotMark备份")
        self.assertRegex(backup.parent.name, r"^类别替换_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_")
        self.assertEqual((backup / "classes.txt").read_bytes(), categories)
        self.assertEqual((backup / "class_attributes.json").read_bytes(), attrs)
        self.assertEqual((backup / "road.json").read_bytes(), original)
        self.assertEqual(annotation.read_bytes(), original)
        self.assertEqual((self.project / "class_attributes.json").read_bytes(), attrs)
        self.assertEqual(self.win.class_list, ["new", "second"])
        self.assertEqual(self.win.classListWidget.get_class_list(), ["new", "second"])
        self.assertEqual(shape.label, "old")
        self.assertEqual(shape.attributes["old_kind"], "value")
        self.assertIn("old", self.win.classListWidget._unlisted_labels)
        self.win.scene.clear_shapes()
        self.win.load_classes(str(self.project))
        self.win.load_annotations(str(self.image))
        self.win.update_annotation_tree()
        self.assertEqual(self.win.class_list, ["new", "second"])
        self.assertIn("old", self.win.classListWidget._unlisted_labels)
        self.assertEqual((self.project / "classes.txt").read_text(encoding="utf-8"), "new\nsecond")
        self.assertEqual(annotation.read_bytes(), original)

    def test_replace_yolo_interprets_existing_ids_without_rewriting_file(self):
        self.win.save_classes()
        annotation = self.project / "road.txt"
        annotation.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        before = annotation.read_bytes()
        self.win.current_format = "yolo"
        self.win.load_annotations(str(self.image))
        self.win._remember_current_annotation_signature()
        source = self._source("categories.txt", "new\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        labels = [getattr(item, "label", "") for item in self.win.scene.items()]
        self.assertIn("new", labels)
        self.assertNotIn("old", labels)
        self.assertEqual(annotation.read_bytes(), before)
        self.assertEqual((Path(self.win._last_category_backup) / "project" / "road.txt").read_bytes(), before)
        self.assertTrue(self.win.save_annotation("yolo"))
        self.assertEqual(annotation.read_bytes(), before)

    def test_yolo_out_of_range_id_is_not_silently_dropped_on_save(self):
        from PySide6.QtCore import QPointF
        self.win.add_class_to_list("second")
        self.win.save_classes()
        annotation = self.project / "road.txt"
        annotation.write_text("1 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        original = annotation.read_bytes()
        self.win.current_format = "yolo"
        self.win.load_annotations(str(self.image))
        self.win._remember_current_annotation_signature()
        source = self._source("categories.txt", "new\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        shape = next(item for item in self.win.scene.items() if getattr(item, "label", "") == "1")
        shape.setPos(QPointF(5, 5))
        self.win.auto_save_annotation()
        self.assertFalse(self.win._flush_auto_save_annotation())
        self.assertIn("不在当前类别列表", self.win._last_autosave_error)
        self.assertEqual(annotation.read_bytes(), original)

    def test_project_class_marker_is_not_a_conversion_annotation(self):
        from jotmark.data.category_conversion import annotation_json_paths
        (self.project / ".jotmark_explicit_classes.json").write_text('{"version": 1}', encoding="utf-8")
        (self.project / "road.json").write_text('{"shapes": []}', encoding="utf-8")
        self.assertEqual([path.name for path in annotation_json_paths(self.project)], ["road.json"])

    def test_replace_xml_keeps_old_label_visible_after_reopen(self):
        self.win.save_classes()
        annotation = self.project / "road.xml"
        annotation.write_text("<annotation><object><name>old</name><bndbox><xmin>10</xmin><ymin>10</ymin>"
                              "<xmax>30</xmax><ymax>30</ymax></bndbox></object></annotation>", encoding="utf-8")
        original = annotation.read_bytes()
        self.win.current_format = "xml"
        self.win.load_annotations(str(self.image))
        self.win._remember_current_annotation_signature()
        source = self._source("categories.txt", "new\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        self.win.scene.clear_shapes()
        self.win.load_classes(str(self.project))
        self.win.load_annotations(str(self.image))
        self.win.update_annotation_tree()
        self.assertEqual(self.win.class_list, ["new"])
        self.assertIn("old", self.win.classListWidget._unlisted_labels)
        self.assertEqual(annotation.read_bytes(), original)
        self.assertEqual((Path(self.win._last_category_backup) / "project" / "road.xml").read_bytes(), original)

    def test_replace_backs_up_pending_canvas_before_changing_classes(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        from jotmark.graphics.shapes import PolyShape
        self.win.save_classes()
        shape = PolyShape(QPolygonF([QPointF(10, 10), QPointF(40, 10), QPointF(30, 40)]), "old")
        shape.attributes = {"value": "unsaved"}
        self.win.scene.addItem(shape)
        self.win.auto_save_annotation()
        source = self._source("categories.txt", "new\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        backup = Path(self.win._last_category_backup) / "project" / "road.json"
        saved = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(saved["shapes"][0]["label"], "old")
        self.assertEqual(saved["shapes"][0]["attributes"]["value"], "unsaved")
        self.assertEqual(self.win.class_list, ["new"])

    def test_replace_does_not_overwrite_an_unreadable_attribute_file(self):
        self.win.save_classes()
        path = self.project / "class_attributes.json"
        path.write_text('{"global": "broken"}', encoding="utf-8")
        self.win.attribute_store.load(str(self.project))
        self.assertTrue(self.win.attribute_store.load_error)
        source = self._source("categories.txt", "new\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        self.assertEqual(self.win.class_list, ["new"])
        self.assertEqual(path.read_text(encoding="utf-8"), '{"global": "broken"}')
        self.assertEqual((Path(self.win._last_category_backup) / "project" / path.name).read_bytes(), path.read_bytes())

    def test_replacement_backs_up_separate_annotation_output(self):
        self.win.save_classes()
        output = self.root / "标注输出"
        output.mkdir()
        self.win.annotation_output_dir = str(output)
        annotation = output / "road.json"
        annotation.write_text('{"shapes": []}', encoding="utf-8")
        source = self._source("categories.txt", "new\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        backup = Path(self.win._last_category_backup)
        self.assertEqual((backup / "annotations" / "road.json").read_bytes(), annotation.read_bytes())
        self.assertEqual((backup / "project" / "classes.txt").read_text(encoding="utf-8"), "old")

    def test_category_backup_failure_cancels_replacement(self):
        self.win.save_classes()
        before = (self.project / "classes.txt").read_bytes()
        source = self._source("categories.txt", "new\n")
        self._choose_dialog_button("替换当前类别")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
             patch("main.QMessageBox.warning"), patch.object(self.win, "_backup_category_operation", side_effect=OSError("backup failed")):
            self.win.import_class_file()
        self.assertEqual(self.win.class_list, ["old"])
        self.assertEqual((self.project / "classes.txt").read_bytes(), before)
        self.assertFalse((self.project / ".jotmark_explicit_classes.json").exists())

    def test_new_project_can_replace_classes(self):
        source = self._source("categories.txt", "new\n")
        captured = []
        self._choose_dialog_button("替换当前类别", captured)
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.DialogOver"):
            self.win.import_class_file()
        self.assertIn("替换当前类别", captured[0][2])
        self.assertEqual(self.win.class_list, ["new"])
        self.assertEqual((self.project / "classes.txt").read_text(encoding="utf-8"), "new")

    def test_non_utf8_class_source_reports_encoding_and_preserves_classes(self):
        source = self.root / "wrong-encoding.txt"
        source.write_bytes(b"\xff")
        with patch("main.QFileDialog.getOpenFileName", return_value=(str(source), "")), patch("main.QMessageBox.warning"):
            self.win.import_class_file()
        self.assertIn("文件编码不受支持", self.win._last_runtime_error)
        self.assertEqual(self.win.class_list, ["old"])


if __name__ == "__main__":
    unittest.main()
