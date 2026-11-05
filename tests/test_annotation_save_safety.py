"""Failure-injection tests for annotation persistence and recovery."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for annotation safety tests")
class AnnotationSaveSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings_dir = tempfile.TemporaryDirectory()
        cls.previous = {key: os.environ.get(key) for key in ("QT_QPA_PLATFORM", "XDG_CONFIG_HOME", "JOTMARK_MANUAL_ONLY")}
        os.environ.update({"QT_QPA_PLATFORM": "offscreen", "XDG_CONFIG_HOME": cls.settings_dir.name, "JOTMARK_MANUAL_ONLY": "1"})
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.settings_dir.cleanup()
        for key, value in cls.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        from PySide6.QtGui import QImage
        from main import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / "road.png"
        self.assertTrue(QImage(200, 150, QImage.Format_RGB32).save(str(self.image)))
        self.win = MainWindow()
        self.win.current_image_path = str(self.image)
        self.win.scene.load_image(str(self.image))
        self.win.add_class_to_list("boundary")

    def tearDown(self):
        self.win._autosave_timer.stop()
        self.win._autosave_dirty = False
        self.win.close()
        self.app.processEvents()
        self.temp.cleanup()

    def _polygon(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        from jotmark.graphics.shapes import PolyShape
        shape = PolyShape(QPolygonF([QPointF(10, 10), QPointF(30, 10), QPointF(20, 30)]), "boundary")
        shape.attributes = {"visibility": ["visible"]}
        self.win.scene.addItem(shape)
        return shape

    def _load_source(self):
        self.win.load_annotations(str(self.image))
        self.win._remember_current_annotation_signature()

    def test_save_failure_keeps_canvas_and_blocks_format_change_and_close(self):
        self._polygon()
        self.win.auto_save_annotation()
        with patch("main.Exporter.save_json", side_effect=OSError("disk full")), \
             patch("main.QMessageBox.warning"):
            self.assertFalse(self.win._flush_auto_save_annotation())
            self.assertTrue(self.win._autosave_dirty)
            self.assertFalse(self.win.saveFailureLabel.isHidden())
            self.win.set_current_format("xml")
            self.assertEqual(self.win.current_format, "json")
            self.win.set_annotation_output_dir(str(self.root / "other-output"))
            self.assertEqual(self.win.annotation_output_dir, "")
            self.assertEqual(len(self.win._selected_annotation_shapes()), 0)
            self.assertEqual(len([s for s in self.win.scene.items() if getattr(s, "label", None) == "boundary"]), 1)
            self.assertFalse(self.win.close())
            self.assertEqual(self.win.current_image_path, str(self.image))

    def test_save_failure_reverts_file_selection(self):
        from PySide6.QtGui import QImage
        second = self.root / "second.png"
        self.assertTrue(QImage(100, 100, QImage.Format_RGB32).save(str(second)))
        self.win.listFiles.blockSignals(True)
        first_item = self.win._create_file_list_item(str(self.image))
        second_item = self.win._create_file_list_item(str(second))
        self.win.listFiles.addItem(first_item)
        self.win.listFiles.addItem(second_item)
        self.win.listFiles.setCurrentItem(first_item)
        self.win.listFiles.blockSignals(False)
        self._polygon()
        self.win.auto_save_annotation()
        with patch("main.Exporter.save_json", side_effect=OSError("disk full")), \
             patch("main.QMessageBox.warning"):
            self.win.listFiles.setCurrentItem(second_item)
        self.assertIs(self.win.listFiles.currentItem(), first_item)
        self.assertEqual(self.win.current_image_path, str(self.image))
        self.assertTrue(self.win._autosave_dirty)

    def test_recovery_copy_keeps_attributes_without_overwriting_original(self):
        original = self.root / "road.json"
        original.write_text("original bytes", encoding="utf-8")
        self._polygon()
        recovery = self.root / "recovery.json"
        with patch("main.QFileDialog.getSaveFileName", return_value=(str(recovery), "JSON")), \
             patch("main.QMessageBox.information"), patch("main.QMessageBox.warning"):
            self.assertTrue(self.win.save_recovery_snapshot())
        self.assertEqual(original.read_text(encoding="utf-8"), "original bytes")
        self.assertEqual(json.loads(recovery.read_text(encoding="utf-8"))["shapes"][0]["attributes"]["visibility"], ["visible"])

    def test_after_verified_recovery_user_can_explicitly_close(self):
        from PySide6.QtWidgets import QMessageBox
        self._polygon()
        self.win.auto_save_annotation()
        recovery = self.root / "recovery.json"
        with patch("main.Exporter.save_json", side_effect=OSError("disk full")):
            self.assertFalse(self.win._flush_auto_save_annotation())
        with patch("main.QFileDialog.getSaveFileName", return_value=(str(recovery), "JSON")), \
             patch("main.QMessageBox.information"):
            self.assertTrue(self.win.save_recovery_snapshot())
        with patch("main.Exporter.save_json", side_effect=OSError("disk full")), \
             patch("main.QMessageBox.warning"), \
             patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
            self.assertTrue(self.win.close())

    def test_changed_recovery_file_does_not_allow_unsafe_close(self):
        from PySide6.QtWidgets import QMessageBox
        self._polygon()
        self.win.auto_save_annotation()
        recovery = self.root / "recovery.json"
        with patch("main.QFileDialog.getSaveFileName", return_value=(str(recovery), "JSON")), \
             patch("main.QMessageBox.information"):
            self.assertTrue(self.win.save_recovery_snapshot())
        recovery.write_text("modified elsewhere", encoding="utf-8")
        with patch("main.Exporter.save_json", side_effect=OSError("disk full")), \
             patch("main.QMessageBox.warning"), \
             patch("main.QMessageBox.question", return_value=QMessageBox.Yes) as question:
            self.assertFalse(self.win.close())
        question.assert_not_called()
        self.assertTrue(self.win._autosave_dirty)

    def test_corrupt_or_unsupported_json_cannot_be_overwritten(self):
        for content in ("{broken", json.dumps({"shapes": [{"label": "x", "shape_type": "circle", "points": [[1, 2], [4, 5]]}]}),
                        '{"shapes":[{"label":"x","shape_type":"polygon","points":[[1e999,0],[1,2],[2,3]]}]}',
                        '{"imagePath":"different.png","shapes":[]}'):
            with self.subTest(content=content):
                path = self.root / "road.json"
                path.write_text(content, encoding="utf-8")
                self.win.scene.clear_shapes()
                self._load_source()
                self._polygon()
                self.win.auto_save_annotation()
                self.assertFalse(self.win._flush_auto_save_annotation())
                self.assertEqual(path.read_text(encoding="utf-8"), content)
                self.win._autosave_dirty = False

    def test_incomplete_json_load_does_not_add_partial_shapes(self):
        path = self.root / "road.json"
        source = {"shapes": [
            {"label": "boundary", "shape_type": "polygon", "points": [[10, 10], [20, 10], [15, 20]]},
            {"label": "boundary", "shape_type": "circle", "points": [[1, 2], [3, 4]]},
        ]}
        path.write_text(json.dumps(source), encoding="utf-8")
        self._load_source()
        self.assertFalse(any(getattr(s, "label", None) == "boundary" for s in self.win.scene.items()))
        self.assertIn(os.path.abspath(path), self.win._protected_annotation_paths)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), source)

    def test_external_modification_is_not_overwritten(self):
        path = self.root / "road.json"
        original = {"shapes": []}
        path.write_text(json.dumps(original), encoding="utf-8")
        self._load_source()
        self._polygon()
        self.win.auto_save_annotation()
        external = '{"shapes": [], "external": true}'
        path.write_text(external, encoding="utf-8")
        self.assertFalse(self.win._flush_auto_save_annotation())
        self.assertEqual(path.read_text(encoding="utf-8"), external)
        self.assertIn("其他程序修改", self.win._last_autosave_error)

    def test_previous_version_and_empty_annotation_are_recoverable(self):
        path = self.root / "road.json"
        self._polygon()
        self.win.auto_save_annotation()
        self.assertTrue(self.win._flush_auto_save_annotation())
        first = path.read_bytes()
        shape = next(s for s in self.win.scene.items() if getattr(s, "label", None) == "boundary")
        shape.attributes["visibility"] = ["uncertain"]
        self.win.auto_save_annotation()
        self.assertTrue(self.win._flush_auto_save_annotation())
        previous = self.root / ".jotmark_backups" / "annotation-history" / "road.json.previous"
        self.assertEqual(previous.read_bytes(), first)
        with patch("main.DialogOver"):
            self.assertTrue(self.win.save_annotation("json"))
        self.assertEqual(previous.read_bytes(), first)
        second = path.read_bytes()
        self.win.scene.clear_shapes()
        self.win.auto_save_annotation()
        self.assertTrue(self.win._flush_auto_save_annotation())
        self.assertFalse(path.exists())
        trash = list((self.root / ".jotmark_trash" / "annotations").glob("road-*.json"))
        self.assertEqual(len(trash), 1)
        self.assertEqual(trash[0].read_bytes(), second)

    def test_previous_backup_failure_preserves_existing_file(self):
        path = self.root / "road.json"
        self._polygon()
        self.win.auto_save_annotation()
        self.assertTrue(self.win._flush_auto_save_annotation())
        before = path.read_bytes()
        shape = next(s for s in self.win.scene.items() if getattr(s, "label", None) == "boundary")
        shape.attributes["visibility"] = ["uncertain"]
        self.win.auto_save_annotation()
        with patch("main.backup_previous_annotation", side_effect=OSError("backup unavailable")):
            self.assertFalse(self.win._flush_auto_save_annotation())
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.win._autosave_dirty)

    def test_failed_archive_keeps_original_and_unsaved_state(self):
        path = self.root / "road.json"
        self._polygon()
        self.win.auto_save_annotation()
        self.assertTrue(self.win._flush_auto_save_annotation())
        before = path.read_bytes()
        self.win.scene.clear_shapes()
        self.win.auto_save_annotation()
        with patch("main.archive_annotation", side_effect=OSError("read only")):
            self.assertFalse(self.win._flush_auto_save_annotation())
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.win._autosave_dirty)

    def test_external_change_during_backup_is_not_overwritten(self):
        from jotmark.data.annotation_safety import backup_previous_annotation
        path = self.root / "road.json"
        self._polygon()
        self.win.auto_save_annotation()
        self.assertTrue(self.win._flush_auto_save_annotation())
        shape = next(s for s in self.win.scene.items() if getattr(s, "label", None) == "boundary")
        shape.attributes["visibility"] = ["uncertain"]
        self.win.auto_save_annotation()
        external = '{"shapes": [], "changed_elsewhere": true}'
        def change_while_backing_up(target):
            result = backup_previous_annotation(target)
            path.write_text(external, encoding="utf-8")
            return result
        with patch("main.backup_previous_annotation", side_effect=change_while_backing_up):
            self.assertFalse(self.win._flush_auto_save_annotation())
        self.assertEqual(path.read_text(encoding="utf-8"), external)

    def test_json_metadata_survives_save_undo_and_redo(self):
        from jotmark.data.exporter import Exporter
        path = self.root / "road.json"
        original = {
            "version": "custom-1", "flags": {"reviewed": True}, "custom_document": {"id": 7},
            "imagePath": "road.png", "imageHeight": 150, "imageWidth": 200,
            "shapes": [{"label": "boundary", "shape_type": "polygon",
                        "points": [[10, 10], [30, 10], [20, 30]], "group_id": 42,
                        "flags": {"occluded": True}, "custom_shape": {"source": "camera"},
                        "attributes": {"visibility": ["visible"]}}],
        }
        path.write_text(json.dumps(original), encoding="utf-8")
        self._load_source()
        self.win.push_state()
        shape = next(s for s in self.win.scene.items() if getattr(s, "label", None) == "boundary")
        shape.attributes["visibility"] = ["uncertain"]
        self.win.scene.state_changed.emit()
        self.win.undo()
        self.assertEqual(Exporter.extract_shapes(self.win.scene)[0]["_json_metadata"]["group_id"], 42)
        self.win.redo()
        self.assertTrue(self.win._flush_auto_save_annotation())
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["custom_document"], {"id": 7})
        self.assertEqual(saved["flags"], {"reviewed": True})
        self.assertEqual(saved["shapes"][0]["group_id"], 42)
        self.assertEqual(saved["shapes"][0]["flags"], {"occluded": True})
        self.assertEqual(saved["shapes"][0]["custom_shape"], {"source": "camera"})

    def test_corrupt_attribute_schema_cannot_be_silently_overwritten(self):
        from jotmark.data.attribute_schema import ClassAttributeStore
        path = self.root / "class_attributes.json"
        broken = '{"global": "wrong format"}'
        path.write_text(broken, encoding="utf-8")
        store = ClassAttributeStore()
        store.load(str(self.root))
        self.assertTrue(store.load_error)
        with self.assertRaises(RuntimeError):
            store.save()
        self.assertEqual(path.read_text(encoding="utf-8"), broken)
        duplicate = {"global": [{"id": "duplicate", "name": "one"}],
                     "classes": {"boundary": [{"id": "duplicate", "name": "two"}]}}
        path.write_text(json.dumps(duplicate), encoding="utf-8")
        store.load(str(self.root))
        self.assertIn("重复", store.load_error)
        with self.assertRaises(RuntimeError):
            store.save()

    def test_attribute_schema_external_change_blocks_edit_and_restores_memory(self):
        from jotmark.data.attribute_schema import ClassAttributeStore
        path = self.root / "class_attributes.json"
        initial = {"global": [{"id": "original", "name": "Original", "type": "text"}], "classes": {}}
        path.write_text(json.dumps(initial), encoding="utf-8")
        self.win.current_dir = str(self.root)
        self.win.attribute_store.load(str(self.root))
        before = self.win.attribute_store.global_attributes()
        external = '{"global": [], "classes": {}, "changed_elsewhere": true}'
        path.write_text(external, encoding="utf-8")
        with patch("main.QMessageBox.warning"):
            self.assertFalse(self.win._apply_attribute_schema_edit("global", "", []))
        self.assertEqual(path.read_text(encoding="utf-8"), external)
        self.assertEqual(self.win.attribute_store.global_attributes(), before)
        self.assertIsInstance(self.win.attribute_store, ClassAttributeStore)

    def test_attribute_schema_external_change_during_backup_blocks_write(self):
        from jotmark.data.annotation_safety import backup_previous_annotation
        from jotmark.data.attribute_schema import ClassAttributeStore
        path = self.root / "class_attributes.json"
        path.write_text('{"global": [], "classes": {}}', encoding="utf-8")
        store = ClassAttributeStore()
        store.load(str(self.root))
        store.set_global_attributes([{"id": "new", "name": "new", "type": "text"}])
        external = '{"global": [], "classes": {}, "changed_elsewhere": true}'

        def change_while_backing_up(target):
            result = backup_previous_annotation(target)
            path.write_text(external, encoding="utf-8")
            return result

        with patch("jotmark.data.attribute_schema.backup_previous_annotation", side_effect=change_while_backing_up):
            with self.assertRaisesRegex(RuntimeError, "备份期间"):
                store.save()
        self.assertEqual(path.read_text(encoding="utf-8"), external)
        self.assertEqual(store.global_attributes()[0]["id"], "new")

    def test_schema_save_preserves_unknown_metadata_at_all_levels(self):
        from jotmark.data.attribute_schema import ClassAttributeStore
        path = self.root / "class_attributes.json"
        source = {"version": 7, "custom_document": {"source": "audit"}, "classes": {},
                  "global": [{"id": "a", "name": "A", "type": "text", "custom": {"id": 9},
                              "children": [{"id": "b", "name": "B", "type": "text", "custom_child": True}]}]}
        path.write_text(json.dumps(source), encoding="utf-8")
        store = ClassAttributeStore()
        store.load(str(self.root))
        nodes = store.global_attributes()
        nodes[0]["name"] = "Renamed"
        store.set_global_attributes(nodes)
        store.save()
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 7)
        self.assertEqual(saved["custom_document"], source["custom_document"])
        self.assertEqual(saved["global"][0]["custom"], {"id": 9})
        self.assertTrue(saved["global"][0]["children"][0]["custom_child"])

    def test_class_replacement_schema_save_failure_keeps_classes_and_attributes(self):
        self.win.attribute_store.set_attributes("boundary", [{"id": "kind", "name": "kind", "type": "text"}])
        before = self.win.attribute_store.attributes_for("boundary")
        with patch.object(self.win.attribute_store, "save", side_effect=OSError("read only")):
            with self.assertRaisesRegex(OSError, "read only"):
                self.win._apply_class_names(["replacement"], replace=True)
        self.assertEqual(self.win.class_list, ["boundary"])
        self.assertEqual(self.win.classListWidget.get_class_list(), ["boundary"])
        self.assertEqual(self.win.attribute_store.attributes_for("boundary"), before)

    def test_attribute_schema_edit_preserves_unrelated_legacy_values_and_backup(self):
        self.win.current_dir = str(self.root)
        self.win.attribute_store.directory = str(self.root)
        old_nodes = [{"id": "old", "name": "old", "type": "text"},
                     {"id": "keep", "name": "keep", "type": "text"}]
        self.win.attribute_store.set_global_attributes(old_nodes)
        self.win.attribute_store.save()
        path = self.root / "road.json"
        source = {"shapes": [{"label": "boundary", "shape_type": "polygon",
                  "points": [[10, 10], [30, 10], [20, 30]],
                  "attributes": {"old": "remove", "keep": "stay", "legacy": "must stay"}}]}
        path.write_text(json.dumps(source), encoding="utf-8")
        self._load_source()
        self.assertTrue(self.win._apply_attribute_schema_edit("global", "", [old_nodes[1]]))
        current = json.loads(path.read_text(encoding="utf-8"))["shapes"][0]["attributes"]
        self.assertEqual(current, {"keep": "stay", "legacy": "must stay"})
        snapshot = Path(self.win._last_category_backup) / "project" / "road.json"
        self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8")), source)

    def test_attribute_schema_save_failure_restores_in_memory_configuration(self):
        nodes = [{"id": "original", "name": "original", "type": "text"}]
        self.win.attribute_store.set_global_attributes(nodes)
        before = self.win.attribute_store.global_attributes()
        with patch.object(self.win.attribute_store, "save", side_effect=OSError("read only")), \
             patch("main.QMessageBox.warning"):
            self.assertFalse(self.win._apply_attribute_schema_edit("global", "", []))
        self.assertEqual(self.win.attribute_store.global_attributes(), before)

    def test_class_rename_creates_snapshot_before_changing_sidecar(self):
        self.win.current_dir = str(self.root)
        self.win.attribute_store.directory = str(self.root)
        path = self.root / "road.json"
        source = {"shapes": [{"label": "boundary", "shape_type": "polygon",
                  "points": [[10, 10], [30, 10], [20, 30]], "attributes": {}}]}
        path.write_text(json.dumps(source), encoding="utf-8")
        self._load_source()
        with patch("main.DialogOver"):
            self.assertTrue(self.win.classListWidget.rename_class_in_data("boundary", "edge"))
        snapshot = Path(self.win._last_category_backup) / "project" / "road.json"
        self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8")), source)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["shapes"][0]["label"], "edge")

    def test_class_rename_fails_safely_when_schema_cannot_be_saved(self):
        self.win.current_dir = str(self.root)
        self.win.attribute_store.set_attributes("boundary", [{"id": "kind", "name": "kind", "type": "text"}])
        with patch.object(self.win.attribute_store, "save", side_effect=OSError("read only")), \
             patch("main.QMessageBox.warning"):
            self.assertFalse(self.win.classListWidget.rename_class_in_data("boundary", "edge"))
        self.assertIn("boundary", self.win.classListWidget.get_class_list())
        self.assertNotIn("edge", self.win.classListWidget.get_class_list())
        self.assertTrue(self.win.attribute_store.attributes_for("boundary"))

    def test_class_rename_fails_safely_when_snapshot_cannot_be_created(self):
        self.win.current_dir = str(self.root)
        with patch.object(self.win, "_backup_category_operation", side_effect=OSError("disk full")), \
             patch("main.QMessageBox.warning"):
            self.assertFalse(self.win.classListWidget.rename_class_in_data("boundary", "edge"))
        self.assertIn("boundary", self.win.classListWidget.get_class_list())
        self.assertNotIn("edge", self.win.classListWidget.get_class_list())

    def test_class_deletion_does_not_touch_unrelated_or_curvelanes_text(self):
        self.win.current_dir = str(self.root)
        label = self.root / "road.txt"
        label.write_text("0 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.1 0.1", encoding="utf-8")
        lane = self.root / "road.lines.txt"
        lane.write_text("0 10 20 30", encoding="utf-8")
        notes = self.root / "notes.txt"
        notes.write_text("0 unrelated notes", encoding="utf-8")
        stats = self.win._delete_class_from_saved_annotations("boundary", 0, str(self.root))
        self.assertEqual(stats["yolo_lines"], 1)
        self.assertEqual(lane.read_text(encoding="utf-8"), "0 10 20 30")
        self.assertEqual(notes.read_text(encoding="utf-8"), "0 unrelated notes")
        self.assertEqual(label.read_text(encoding="utf-8"), "0 0.5 0.5 0.1 0.1")

    def test_class_deletion_blocks_external_change_during_backup_in_all_formats(self):
        from PySide6.QtGui import QImage
        from jotmark.data.annotation_safety import backup_previous_annotation
        formats = {
            "json": '{"shapes": [{"label": "boundary"}]}',
            "xml": '<annotation><object><name>boundary</name></object></annotation>',
            "txt": '0 0.5 0.5 0.2 0.2',
        }
        for extension, original in formats.items():
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.assertTrue(QImage(10, 10, QImage.Format_RGB32).save(str(root / "sample.png")))
                path = root / ("sample." + extension)
                path.write_text(original, encoding="utf-8")
                self.win.current_dir = directory
                external = "externally changed " + extension

                def change_while_backing_up(target):
                    result = backup_previous_annotation(target)
                    path.write_text(external, encoding="utf-8")
                    return result

                with patch("main.backup_previous_annotation", side_effect=change_while_backing_up):
                    stats = self.win._delete_class_from_saved_annotations("boundary", 0, directory)
                self.assertEqual(path.read_text(encoding="utf-8"), external)
                self.assertEqual(len(stats["errors"]), 1)
                self.assertIn("备份期间", stats["errors"][0])

    def test_removed_attribute_prune_blocks_external_change_during_backup(self):
        from jotmark.data.annotation_safety import backup_previous_annotation
        self.win.current_dir = str(self.root)
        path = self.root / "road.json"
        path.write_text('{"shapes": [{"label": "boundary", "attributes": {"old": "value"}}]}', encoding="utf-8")
        external = '{"shapes": [], "changed_elsewhere": true}'

        def change_while_backing_up(target):
            result = backup_previous_annotation(target)
            path.write_text(external, encoding="utf-8")
            return result

        with patch("main.backup_previous_annotation", side_effect=change_while_backing_up), patch("main.DialogOver") as warning:
            self.win._prune_removed_attribute_values(None, {"old"})
        self.assertEqual(path.read_text(encoding="utf-8"), external)
        warning.assert_called_once()

    def test_class_rename_blocks_external_change_during_backup(self):
        from jotmark.data.annotation_safety import backup_previous_annotation
        self.win.current_dir = str(self.root)
        self.win.attribute_store.load(str(self.root))
        path = self.root / "road.json"
        path.write_text('{"shapes": [{"label": "boundary"}]}', encoding="utf-8")
        external = '{"shapes": [], "changed_elsewhere": true}'

        def change_while_backing_up(target):
            result = backup_previous_annotation(target)
            if os.path.abspath(target) == str(path):
                path.write_text(external, encoding="utf-8")
            return result

        with patch("main.backup_previous_annotation", side_effect=change_while_backing_up), patch("main.DialogOver") as warning:
            self.assertTrue(self.win.classListWidget.rename_class_in_data("boundary", "edge"))
        self.assertEqual(path.read_text(encoding="utf-8"), external)
        self.assertIn("部分更新失败", warning.call_args.args)

    def test_same_stem_images_do_not_share_and_overwrite_a_sidecar(self):
        (self.root / "road.jpg").write_bytes(b"another image")
        self._polygon()
        self.win.auto_save_annotation()
        self.assertFalse(self.win._flush_auto_save_annotation())
        self.assertIn("共用同名标注文件", self.win._last_autosave_error)
        self.assertFalse((self.root / "road.json").exists())

    def test_failed_file_move_rolls_back_and_keeps_current_canvas(self):
        import shutil
        self.win.current_dir = str(self.root)
        self.win._rebuild_file_list([str(self.image)])
        item = self.win.listFiles.item(0)
        sidecar = self.root / "road.json"
        sidecar.write_text("original", encoding="utf-8")
        original_move = shutil.move
        def fail_sidecar(source, destination):
            if str(source) == str(sidecar):
                raise OSError("simulated sidecar failure")
            return original_move(source, destination)
        with patch("main.shutil.move", side_effect=fail_sidecar), patch("main.QMessageBox.warning") as warning:
            self.win.delete_selected_files([item])
        warning.assert_called_once()
        self.assertTrue(self.image.exists())
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "original")
        self.assertEqual(self.win.current_image_path, str(self.image))
        self.assertIsNotNone(self.win.scene.img_item)
        self.assertEqual(self.win.listFiles.count(), 1)

    def test_moved_point_exports_actual_scene_coordinate(self):
        from PySide6.QtCore import QPointF
        from jotmark.data.exporter import Exporter
        from jotmark.graphics.shapes import PointShape
        point = PointShape(QPointF(10, 20), "boundary")
        self.win.scene.addItem(point)
        point.setPos(30, 40)
        self.assertEqual(Exporter.extract_shapes(self.win.scene)[0]["points"], [[40.0, 60.0]])

    def test_invalid_numeric_json_does_not_replace_existing_file(self):
        from jotmark.data.exporter import Exporter
        path = self.root / "road.json"
        path.write_text("original", encoding="utf-8")
        shapes = [{"label": "boundary", "type": "point", "points": [[float("nan"), 1]]}]
        with self.assertRaises(ValueError):
            Exporter.save_json(str(path), str(self.image), 200, 150, shapes)
        self.assertEqual(path.read_text(encoding="utf-8"), "original")

    def test_yolo_batch_json_append_preserves_existing_metadata_and_backup(self):
        from PySide6.QtCore import QRectF
        from main import YoloBatchPredictorWorker
        path = self.root / "road.json"
        old = {"version": "legacy", "custom": "keep", "shapes": [
            {"label": "boundary", "shape_type": "polygon", "points": [[10, 10], [30, 10], [20, 30]],
             "group_id": 9, "flags": {"x": True}, "attributes": {"note": "keep"}, "extra": "keep"}
        ]}
        path.write_text(json.dumps(old), encoding="utf-8")
        predictor = Mock()
        predictor.predict_sync.return_value = [{"type": "rect", "label": "boundary", "data": QRectF(80, 80, 10, 10)}]
        worker = YoloBatchPredictorWorker(predictor, [str(self.image)], "json", ["boundary"])
        errors = []
        worker.error.connect(errors.append)
        worker.run()
        self.assertEqual(errors, [])
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["custom"], "keep")
        self.assertEqual(saved["shapes"][0]["flags"], {"x": True})
        self.assertEqual(saved["shapes"][0]["group_id"], 9)
        self.assertEqual(saved["shapes"][0]["attributes"], {"note": "keep"})
        self.assertEqual(saved["shapes"][0]["extra"], "keep")
        self.assertEqual(len(saved["shapes"]), 2)
        previous = self.root / ".jotmark_backups" / "annotation-history" / "road.json.previous"
        self.assertEqual(json.loads(previous.read_text(encoding="utf-8")), old)

    def test_sam_batch_json_append_preserves_existing_metadata(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        import numpy as np
        from main import SamBatchWorker
        path = self.root / "road.json"
        old = {"custom": "keep", "shapes": [{"label": "boundary", "shape_type": "polygon",
                "points": [[10, 10], [30, 10], [20, 30]], "group_id": 9, "flags": {"x": True}}]}
        path.write_text(json.dumps(old), encoding="utf-8")
        processor = Mock()
        processor.set_image.return_value = {}
        processor.set_text_prompt.return_value = {"masks": [np.zeros((10, 10))], "scores": [0.9],
                                                  "boxes": [np.array([1, 1, 10, 10])]}
        fake_torch = SimpleNamespace(inference_mode=lambda: nullcontext(), is_tensor=lambda _x: False)
        worker = SamBatchWorker(processor, Mock(), [str(self.image)], ["car"], "json", ["boundary", "car"], 2, device="cpu")
        errors = []
        worker.error.connect(errors.append)
        with patch("main.torch", fake_torch), patch("main.inference_context", return_value=nullcontext()), \
             patch("main.mask_to_annotation_geometry", return_value=([[80, 80], [90, 80], [85, 90]], [80, 80, 10, 10], None)):
            worker.run()
        self.assertEqual(errors, [])
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["custom"], "keep")
        self.assertEqual(saved["shapes"][0]["group_id"], 9)
        self.assertEqual(saved["shapes"][0]["flags"], {"x": True})
        self.assertEqual(len(saved["shapes"]), 2)

    def test_batch_refuses_lossy_append_and_external_changes(self):
        from PySide6.QtCore import QRectF
        from main import YoloBatchPredictorWorker
        yolo_path = self.root / "road.txt"
        yolo_path.write_text("0 0.1 0.1 0.2 0.1 0.2 0.2", encoding="utf-8")
        predictor = Mock()
        worker = YoloBatchPredictorWorker(predictor, [str(self.image)], "yolo", ["boundary"])
        errors = []
        worker.error.connect(errors.append)
        worker.run()
        self.assertTrue(errors)
        predictor.predict_sync.assert_not_called()
        self.assertEqual(yolo_path.read_text(encoding="utf-8"), "0 0.1 0.1 0.2 0.1 0.2 0.2")

        json_path = self.root / "road.json"
        json_path.write_text('{"shapes": []}', encoding="utf-8")
        external = '{"shapes": [], "changed_elsewhere": true}'
        def change_during_prediction(*_args, **_kwargs):
            json_path.write_text(external, encoding="utf-8")
            return [{"type": "rect", "label": "boundary", "data": QRectF(80, 80, 10, 10)}]
        predictor.predict_sync.side_effect = change_during_prediction
        worker = YoloBatchPredictorWorker(predictor, [str(self.image)], "json", ["boundary"])
        errors = []
        worker.error.connect(errors.append)
        worker.run()
        self.assertTrue(errors)
        self.assertEqual(json_path.read_text(encoding="utf-8"), external)


if __name__ == "__main__":
    unittest.main()
