"""Category changes must protect global, category, and legacy attributes."""

import json
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for category reassignment tests")
class ClassReassignmentSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tempfile.TemporaryDirectory()
        cls.previous = {key: os.environ.get(key) for key in ("QT_QPA_PLATFORM", "XDG_CONFIG_HOME", "JOTMARK_MANUAL_ONLY")}
        os.environ.update({"QT_QPA_PLATFORM": "offscreen", "XDG_CONFIG_HOME": cls.config.name, "JOTMARK_MANUAL_ONLY": "1"})
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.config.cleanup()
        for key, value in cls.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QImage, QPolygonF
        from jotmark.graphics.canvas import CanvasMode
        from jotmark.graphics.shapes import PolyShape
        from main import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.image = os.path.join(self.temp.name, "road.png")
        self.assertTrue(QImage(200, 150, QImage.Format_RGB32).save(self.image))
        self.win = MainWindow()
        self.win.current_dir = self.temp.name
        self.win.current_image_path = self.image
        self.win.scene.load_image(self.image)
        self.win.scene.set_mode(CanvasMode.EDIT)
        for name in ("boundary", "car", "truck"):
            self.win.add_class_to_list(name)
        self.schema = {
            "global": [
                {"id": "visibility", "name": "visibility", "type": "multi_select", "options": ["visible", "truncated"]},
                {"id": "review", "name": "review", "type": "boolean"},
                {"id": "global_parent", "name": "parent", "type": "text", "children": [
                    {"id": "global_child", "name": "child", "type": "text"}]},
            ],
            "classes": {
                "boundary": [{"id": "kind", "name": "kind", "type": "text", "children": [
                    {"id": "kind_child", "name": "kind_child", "type": "text"}]}],
                "car": [{"id": "car_type", "name": "car_type", "type": "text"}],
                "truck": [{"id": "truck_type", "name": "truck_type", "type": "text"}],
            },
        }
        self.win.attribute_store.replace_schema(self.schema)
        self.shape = PolyShape(QPolygonF([QPointF(10, 10), QPointF(100, 10), QPointF(80, 100), QPointF(10, 80)]), "boundary")
        self.shape.attributes = {
            "visibility": ["visible", "truncated"], "review": False,
            "global_parent": "x", "global_child": "y", "kind": "edge", "kind_child": "sub",
            "car_type": "old destination value", "truck_type": "old third-class value", "legacy": {"audit": 7},
        }
        self.win.scene.addItem(self.shape)
        self.shape.setSelected(True)
        self.win.update_attribute_editor()
        self.win.push_state()

    def tearDown(self):
        self.win._autosave_timer.stop()
        self.win._autosave_dirty = False
        self.win.close()
        self.app.processEvents()
        self.temp.cleanup()

    def _accept_policy(self, keep=True):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from ui.attribute_widgets import ClassChangeAttributeDialog
        observations = []
        def choose():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, ClassChangeAttributeDialog):
                observations.append((dialog.keep_public.isChecked(), dialog.clear_public.isEnabled()))
                if not keep:
                    dialog.clear_public.setChecked(True)
                dialog.accept()
        QTimer.singleShot(25, choose)
        changed = self.win.on_shape_class_reassigned(self.shape, "car")
        return changed, observations

    def test_keep_public_and_children_clears_all_known_class_values(self):
        changed, observed = self._accept_policy(keep=True)
        self.assertTrue(changed)
        self.assertEqual(observed, [(True, True)])
        self.assertEqual(self.shape.label, "car")
        self.assertEqual(self.shape.attributes, {
            "visibility": ["visible", "truncated"], "review": False,
            "global_parent": "x", "global_child": "y", "legacy": {"audit": 7},
        })

    def test_clear_public_and_class_values_retains_unknown_history(self):
        changed, observed = self._accept_policy(keep=False)
        self.assertTrue(changed)
        self.assertEqual(observed, [(True, True)])
        self.assertEqual(self.shape.label, "car")
        self.assertEqual(self.shape.attributes, {"legacy": {"audit": 7}})

    def test_cancel_from_tree_and_new_category_keeps_everything(self):
        from PySide6.QtWidgets import QDialog
        before = deepcopy(self.shape.attributes)
        with patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Rejected) as dialog:
            self.win.classListWidget.shape_class_reassigned.emit(self.shape, "car")
            self.assertFalse(self.win.on_shape_class_reassigned(self.shape, "new_class"))
        self.assertEqual(dialog.call_count, 2)
        self.assertEqual(self.shape.label, "boundary")
        self.assertEqual(self.shape.attributes, before)
        self.assertNotIn("new_class", self.win.class_list)

    def test_cancel_from_right_editor_restores_combo_value(self):
        from PySide6.QtWidgets import QDialog
        editor = self.win.attributeEditor
        editor.class_label_combo.setCurrentText("car")
        with patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Rejected):
            editor._emit_class_label_change()
        self.assertEqual(self.shape.label, "boundary")
        self.assertEqual(editor.class_label_combo.currentText(), "boundary")
        self.assertEqual(self.shape.attributes["kind"], "edge")

    def test_right_editor_accepts_and_synchronizes_new_class(self):
        from PySide6.QtWidgets import QDialog
        editor = self.win.attributeEditor
        editor.class_label_combo.setCurrentText("car")
        with patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Accepted):
            editor._emit_class_label_change()
        self.assertEqual(self.shape.label, "car")
        self.assertEqual(editor.class_label_combo.currentText(), "car")
        self.assertFalse(self.shape.attributes["review"])
        self.assertNotIn("kind", self.shape.attributes)

    def test_class_values_only_still_require_confirmation(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from ui.attribute_widgets import ClassChangeAttributeDialog
        self.shape.attributes = {"kind": "edge", "kind_child": "sub", "legacy": "retain"}
        observed = []
        def confirm():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, ClassChangeAttributeDialog):
                observed.append((dialog.keep_public.isChecked(), dialog.clear_public.isEnabled()))
                dialog.accept()
        QTimer.singleShot(25, confirm)
        self.assertTrue(self.win.on_shape_class_reassigned(self.shape, "car"))
        self.assertEqual(observed, [(True, False)])
        self.assertEqual(self.shape.attributes, {"legacy": "retain"})

    def test_public_values_only_can_be_cleared_after_confirmation(self):
        self.shape.attributes = {"review": False, "visibility": ["visible"]}
        changed, observed = self._accept_policy(keep=False)
        self.assertTrue(changed)
        self.assertEqual(observed, [(True, True)])
        self.assertEqual(self.shape.attributes, {})

    def test_all_geometry_types_keep_coordinates_when_reclassified(self):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPolygonF
        from PySide6.QtWidgets import QDialog
        from jotmark.data.exporter import Exporter
        from jotmark.graphics.shapes import RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape
        self.win.scene.removeItem(self.shape)
        points = QPolygonF([QPointF(10, 10), QPointF(100, 10), QPointF(80, 100)])
        shapes = [
            RectShape(QRectF(10, 10, 80, 50), "boundary"),
            PolyShape(points, "boundary"),
            PolylineShape(points, "boundary"),
            PointShape(QPointF(50, 50), "boundary"),
            RotatedRectShape(60, 60, 50, 30, 15, "boundary"),
            PoseShape(QRectF(10, 10, 80, 50), {"name": "one", "keypoints": [
                {"name": "center", "default_pos": [0.5, 0.5], "color": "#ff0000"}], "connections": []}, "boundary"),
        ]
        with patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Accepted), \
             patch.object(self.win, "_ensure_pending_annotation_saved", return_value=True):
            for shape in shapes:
                with self.subTest(shape=type(shape).__name__):
                    shape.attributes = {"review": False, "kind": "edge"}
                    self.win.scene.addItem(shape)
                    before = Exporter.extract_shapes(self.win.scene)[0]
                    self.assertTrue(self.win.on_shape_class_reassigned(shape, "car"))
                    after = Exporter.extract_shapes(self.win.scene)[0]
                    self.assertEqual(after.pop("label"), "car")
                    self.assertEqual(after.pop("attributes"), {"review": False})
                    before.pop("label")
                    before.pop("attributes")
                    self.assertEqual(after, before)
                    self.win.scene.removeItem(shape)

    def test_no_known_values_does_not_delete_legacy_or_prompt(self):
        self.shape.attributes = {"legacy": {"audit": 7}}
        with patch("main.ClassChangeAttributeDialog.exec") as dialog:
            self.assertTrue(self.win.on_shape_class_reassigned(self.shape, "car"))
        dialog.assert_not_called()
        self.assertEqual(self.shape.attributes, {"legacy": {"audit": 7}})

    def test_failed_pending_save_or_category_save_prevents_reassignment(self):
        from PySide6.QtWidgets import QDialog
        before = deepcopy(self.shape.attributes)
        with patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Accepted), \
             patch.object(self.win, "_ensure_pending_annotation_saved", return_value=False):
            self.assertFalse(self.win.on_shape_class_reassigned(self.shape, "car"))
        self.assertEqual(self.shape.label, "boundary")
        self.assertEqual(self.shape.attributes, before)
        with patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Accepted), \
             patch.object(self.win, "save_classes", side_effect=OSError("read only")), \
             patch("main.QMessageBox.warning"):
            self.assertFalse(self.win.on_shape_class_reassigned(self.shape, "new_class"))
        self.assertEqual(self.shape.label, "boundary")
        self.assertEqual(self.shape.attributes, before)
        self.assertNotIn("new_class", self.win.class_list)

    def test_corrupt_or_externally_changed_schema_blocks_reclassification(self):
        path = os.path.join(self.temp.name, "class_attributes.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(self.schema, stream)
        self.win.attribute_store.load(self.temp.name)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write('{"global": [], "classes": {}}')
        before = deepcopy(self.shape.attributes)
        with patch("main.QMessageBox.warning") as warning, patch("main.ClassChangeAttributeDialog.exec") as dialog:
            self.assertFalse(self.win.on_shape_class_reassigned(self.shape, "car"))
        warning.assert_called_once()
        dialog.assert_not_called()
        self.assertEqual(self.shape.label, "boundary")
        self.assertEqual(self.shape.attributes, before)
        self.win.attribute_store.load_error = "broken JSON"
        with patch("main.QMessageBox.warning"):
            self.assertFalse(self.win.on_shape_class_reassigned(self.shape, "car"))
        self.assertEqual(self.shape.attributes, before)

    def test_schema_changed_during_confirmation_blocks_reclassification(self):
        from PySide6.QtWidgets import QDialog
        path = os.path.join(self.temp.name, "class_attributes.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(self.schema, stream)
        self.win.attribute_store.load(self.temp.name)
        before = deepcopy(self.shape.attributes)
        def change_schema():
            with open(path, "w", encoding="utf-8") as stream:
                stream.write('{"global": [], "classes": {}}')
            return QDialog.Accepted
        with patch("main.ClassChangeAttributeDialog.exec", side_effect=change_schema), patch("main.QMessageBox.warning"):
            self.assertFalse(self.win.on_shape_class_reassigned(self.shape, "car"))
        self.assertEqual(self.shape.label, "boundary")
        self.assertEqual(self.shape.attributes, before)

    def test_save_backup_undo_and_redo_preserve_chosen_values(self):
        from jotmark.graphics.shapes import PolyShape
        before = deepcopy(self.shape.attributes)
        self.assertTrue(self.win.auto_save_annotation(immediate=True))
        changed, _ = self._accept_policy(keep=True)
        self.assertTrue(changed)
        self.assertTrue(self.win._flush_auto_save_annotation())
        with open(os.path.join(self.temp.name, "road.json"), encoding="utf-8") as stream:
            saved = json.load(stream)["shapes"][0]
        self.assertEqual(saved["label"], "car")
        self.assertEqual(saved["attributes"]["review"], False)
        self.assertEqual(saved["attributes"]["global_child"], "y")
        self.assertNotIn("kind", saved["attributes"])
        backup = os.path.join(self.temp.name, ".jotmark_backups", "annotation-history", "road.json.previous")
        with open(backup, encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["shapes"][0]["attributes"], before)
        self.win.undo()
        restored = next(item for item in self.win.scene.items() if isinstance(item, PolyShape))
        self.assertEqual(restored.label, "boundary")
        self.assertEqual(restored.attributes, before)
        self.win.redo()
        restored = next(item for item in self.win.scene.items() if isinstance(item, PolyShape))
        self.assertEqual(restored.label, "car")
        self.assertNotIn("kind", restored.attributes)
        self.assertEqual(restored.attributes["review"], False)


if __name__ == "__main__":
    unittest.main()
