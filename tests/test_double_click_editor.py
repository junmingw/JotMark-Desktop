"""Interactive regression coverage for canvas double-click editing."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for double-click tests")
class DoubleClickEditorTests(unittest.TestCase):
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
        from PySide6.QtGui import QImage
        from jotmark.graphics.canvas import CanvasMode
        from main import MainWindow
        self.directory = tempfile.TemporaryDirectory()
        self.image = os.path.join(self.directory.name, "road.png")
        self.assertTrue(QImage(240, 180, QImage.Format_RGB32).save(self.image))
        self.win = MainWindow()
        self.win.resize(1100, 700)
        self.win.show()
        self.win.current_dir = self.directory.name
        self.win.current_image_path = self.image
        self.win.add_class_to_list("boundary")
        self.win.add_class_to_list("car")
        self.win.attribute_store.replace_schema({
            "global": [
                {"id": "visibility", "name": "visibility", "type": "multi_select", "options": ["visible", "truncated"]},
                {"id": "parent", "name": "parent", "type": "single_select", "options": ["on"], "children": [
                    {"id": "child", "name": "child", "type": "single_select", "options": ["a", "b"]},
                ]},
                {"id": "reviewed", "name": "reviewed", "type": "boolean"},
            ],
            "classes": {
                "boundary": [{"id": "kind", "name": "boundary_kind", "type": "single_select", "options": ["edge", "curb"]}],
                "car": [{"id": "car_type", "name": "car_type", "type": "text"}],
            },
        })
        self.win.scene.load_image(self.image)
        self.win.view.reset_to_fit()
        self.win.scene.set_mode(CanvasMode.EDIT)
        self.win.shape_double_click_mode = "category_attributes"
        self.app.processEvents()

    def tearDown(self):
        self.win._autosave_timer.stop()
        self.win._autosave_dirty = False
        self.win.close()
        self.app.processEvents()
        self.directory.cleanup()

    def _polygon(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        from jotmark.graphics.shapes import PolyShape
        shape = PolyShape(QPolygonF([QPointF(40, 40), QPointF(160, 40), QPointF(160, 120), QPointF(40, 120)]), "boundary")
        self.win.scene.addItem(shape)
        return shape

    def _quick_dialog(self, shape):
        from ui.attribute_widgets import AnnotationQuickEditDialog
        shape.setSelected(True)
        self.win.update_attribute_editor()
        dialog = AnnotationQuickEditDialog(
            self.win.attributeEditor, shape,
            lambda popup: self.win.edit_shape_label(shape, dialog_parent=popup),
            self.win,
        )
        dialog.show()
        self.app.processEvents()
        return dialog

    def _double_click(self, scene_point):
        # QTest.mouseDClick sends the double-press but not a final release.
        # Complete the real mouse gesture so later hover tests do not inherit
        # a logically held left button from this test class.
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        viewport = self.win.view.viewport()
        position = self.win.view.mapFromScene(scene_point)
        QTest.mouseDClick(viewport, Qt.LeftButton, pos=position)
        QTest.mouseRelease(viewport, Qt.LeftButton, pos=position)
        self.app.processEvents()

    def test_double_click_opens_combined_dialog_for_clicked_shape(self):
        from PySide6.QtCore import QPointF, QRectF, QTimer
        from PySide6.QtWidgets import QApplication
        from jotmark.graphics.shapes import RectShape
        from ui.attribute_widgets import AnnotationQuickEditDialog
        shape = self._polygon()
        other = RectShape(QRectF(180, 130, 20, 20), "car")
        self.win.scene.addItem(other)
        other.setSelected(True)
        seen = []
        def inspect_dialog():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, AnnotationQuickEditDialog):
                seen.append((dialog.shape, set(dialog.attribute_buttons)))
                dialog.close()
        QTimer.singleShot(50, inspect_dialog)
        self._double_click(QPointF(80, 80))
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0][0], shape)
        self.assertIn(("global", "visibility"), seen[0][1])
        self.assertIn(("class", "kind"), seen[0][1])
        self.assertNotIn(("class", "car_type"), seen[0][1])
        self.assertTrue(shape.isSelected())
        self.assertFalse(other.isSelected())

    def test_double_click_supports_all_annotation_geometry_types(self):
        from PySide6.QtCore import QPointF, QRectF, QTimer
        from PySide6.QtGui import QPolygonF
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from jotmark.graphics.shapes import RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape
        from ui.attribute_widgets import AnnotationQuickEditDialog
        points = QPolygonF([QPointF(40, 40), QPointF(160, 40), QPointF(160, 120), QPointF(40, 120)])
        shapes = [
            (RectShape(QRectF(40, 40, 120, 80), "boundary"), QPointF(80, 80)),
            (PolyShape(points, "boundary"), QPointF(80, 80)),
            (PolylineShape(QPolygonF([QPointF(40, 80), QPointF(160, 80)]), "boundary"), QPointF(90, 80)),
            (PointShape(QPointF(80, 80), "boundary"), QPointF(80, 80)),
            (RotatedRectShape(80, 80, 80, 50, 0, "boundary"), QPointF(80, 80)),
            (PoseShape(QRectF(40, 40, 80, 80), {"name": "one", "keypoints": [
                {"name": "center", "default_pos": [0.5, 0.5], "color": "#ff0000"}], "connections": []}, "boundary"), QPointF(80, 80)),
        ]
        for shape, point in shapes:
            with self.subTest(shape=type(shape).__name__):
                self.win.scene.clear_shapes()
                self.win.scene.addItem(shape)
                seen = []
                def close_dialog():
                    dialog = QApplication.activeModalWidget()
                    if isinstance(dialog, AnnotationQuickEditDialog):
                        seen.append(dialog.shape)
                        dialog.close()
                QTimer.singleShot(25, close_dialog)
                self._double_click(point)
                QTest.qWait(35)
                self.assertEqual(seen, [shape])

    def test_setting_only_category_keeps_original_picker(self):
        from PySide6.QtCore import QPointF
        shape = self._polygon()
        self.win.shape_double_click_mode = "category"
        with patch("main.QInputDialog.getItem", return_value=("boundary", False)) as picker, \
             patch("main.AnnotationQuickEditDialog") as combined:
            self._double_click(QPointF(80, 80))
        picker.assert_called_once()
        combined.assert_not_called()
        self.assertTrue(shape.isSelected())

    def test_category_only_mode_also_warns_before_clearing_attributes(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtWidgets import QDialog
        shape = self._polygon()
        shape.attributes = {"visibility": ["visible"]}
        self.win.shape_double_click_mode = "category"
        with patch("main.QInputDialog.getItem", return_value=("car", True)), \
             patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Rejected) as confirm:
            self._double_click(QPointF(80, 80))
        confirm.assert_called_once()
        self.assertEqual(shape.label, "boundary")
        self.assertEqual(shape.attributes, {"visibility": ["visible"]})

    def test_attribute_buttons_values_children_and_cancel(self):
        from PySide6.QtWidgets import QDialog
        shape = self._polygon()
        shape.attributes = {"visibility": ["visible"], "reviewed": False}
        dialog = self._quick_dialog(shape)
        try:
            self.assertIn("否", dialog.attribute_buttons[("global", "reviewed")].text())
            self.assertNotIn(("global", "child"), dialog.attribute_buttons)
            with patch("ui.attribute_widgets.AttributeValuePickerDialog.exec", return_value=QDialog.Rejected):
                dialog.attribute_buttons[("global", "visibility")].click()
            self.assertEqual(shape.attributes["visibility"], ["visible"])
            self.app.processEvents()
            with patch("ui.attribute_widgets.AttributeValuePickerDialog.exec", return_value=QDialog.Accepted), \
                 patch("ui.attribute_widgets.AttributeValuePickerDialog.value", return_value="on"):
                dialog.attribute_buttons[("global", "parent")].click()
            self.app.processEvents()
            self.assertEqual(shape.attributes["parent"], "on")
            self.assertIn(("global", "child"), dialog.attribute_buttons)
            with patch("ui.attribute_widgets.AttributeValuePickerDialog.exec", return_value=QDialog.Accepted), \
                 patch("ui.attribute_widgets.AttributeValuePickerDialog.value", return_value="b"):
                dialog.attribute_buttons[("global", "child")].click()
            self.app.processEvents()
            self.assertEqual(shape.attributes["child"], "b")
            with patch("ui.attribute_widgets.AttributeValuePickerDialog.exec", return_value=QDialog.Accepted), \
                 patch("ui.attribute_widgets.AttributeValuePickerDialog.value", return_value=""):
                dialog.attribute_buttons[("global", "parent")].click()
            self.app.processEvents()
            self.assertNotIn("parent", shape.attributes)
            self.assertNotIn("child", shape.attributes)
            self.assertNotIn(("global", "child"), dialog.attribute_buttons)
        finally:
            dialog.close()

    def test_quick_editor_handles_boolean_text_number_and_independent_shapes(self):
        shape = self._polygon()
        other = self._polygon()
        shape.attributes = {"reviewed": True}
        other.attributes = {"reviewed": True, "kind": "edge"}
        self.win.attribute_store.set_attributes("boundary", [
            {"id": "note", "name": "note", "type": "text"},
            {"id": "score", "name": "score", "type": "number"},
        ])
        dialog = self._quick_dialog(shape)
        try:
            with patch("ui.attribute_widgets.QInputDialog.getItem", return_value=("否", True)):
                dialog.attribute_buttons[("global", "reviewed")].click()
            self.app.processEvents()
            self.assertIs(shape.attributes["reviewed"], False)
            with patch("ui.attribute_widgets.QInputDialog.getText", return_value=("道路", True)):
                dialog.attribute_buttons[("class", "note")].click()
            self.app.processEvents()
            with patch("ui.attribute_widgets.QInputDialog.getText", return_value=("3.5", True)):
                dialog.attribute_buttons[("class", "score")].click()
            self.app.processEvents()
            self.assertEqual(shape.attributes["note"], "道路")
            self.assertEqual(shape.attributes["score"], 3.5)
            with patch("ui.attribute_widgets.QInputDialog.getText", return_value=("invalid", True)), \
                 patch("ui.attribute_widgets.QMessageBox.warning") as warning:
                dialog.attribute_buttons[("class", "score")].click()
            warning.assert_called_once()
            self.assertEqual(shape.attributes["score"], 3.5)
            self.assertEqual(other.attributes, {"reviewed": True, "kind": "edge"})
        finally:
            dialog.close()

    def test_real_nested_picker_saves_and_supports_undo_redo(self):
        from PySide6.QtCore import QPointF, QTimer, Qt
        from PySide6.QtWidgets import QApplication
        from jotmark.graphics.shapes import PolyShape
        from ui.attribute_widgets import AnnotationQuickEditDialog, AttributeValuePickerDialog
        shape = self._polygon()
        shape.attributes = {"visibility": ["visible"]}
        self.win.push_state()
        seen = []
        def choose_value():
            picker = QApplication.activeModalWidget()
            if isinstance(picker, AttributeValuePickerDialog):
                seen.append(picker.parentWidget().__class__.__name__)
                picker.list_widget.item(0).setCheckState(Qt.Unchecked)
                picker.list_widget.item(1).setCheckState(Qt.Checked)
                picker.accept()
        def edit_value():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, AnnotationQuickEditDialog):
                QTimer.singleShot(50, choose_value)
                dialog.attribute_buttons[("global", "visibility")].click()
                dialog.close()
        QTimer.singleShot(50, edit_value)
        self._double_click(QPointF(80, 80))
        self.assertEqual(seen, ["AnnotationQuickEditDialog"])
        self.assertEqual(shape.attributes["visibility"], ["truncated"])
        self.assertTrue(self.win._flush_auto_save_annotation())
        with open(os.path.join(self.directory.name, "road.json"), encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["shapes"][0]["attributes"]["visibility"], ["truncated"])
        self.win.undo()
        restored = next(item for item in self.win.scene.items() if isinstance(item, PolyShape))
        self.assertEqual(restored.attributes["visibility"], ["visible"])
        self.win.redo()
        restored = next(item for item in self.win.scene.items() if isinstance(item, PolyShape))
        self.assertEqual(restored.attributes["visibility"], ["truncated"])

    def test_category_change_warns_before_clearing_attributes_and_refreshes_schema(self):
        from PySide6.QtWidgets import QDialog
        shape = self._polygon()
        shape.attributes = {"visibility": ["visible"], "kind": "edge"}
        dialog = self._quick_dialog(shape)
        try:
            with patch("main.QInputDialog.getItem", return_value=("car", True)), \
                 patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Rejected):
                dialog.category_button.click()
            self.assertEqual(shape.label, "boundary")
            self.assertEqual(shape.attributes["kind"], "edge")
            self.app.processEvents()
            with patch("main.QInputDialog.getItem", return_value=("car", True)), \
                 patch("main.ClassChangeAttributeDialog.exec", return_value=QDialog.Accepted):
                dialog.category_button.click()
            self.app.processEvents()
            self.assertEqual(shape.label, "car")
            self.assertEqual(shape.attributes, {"visibility": ["visible"]})
            self.assertIn("car", dialog.category_button.text())
            self.assertIn(("class", "car_type"), dialog.attribute_buttons)
            self.assertNotIn(("class", "kind"), dialog.attribute_buttons)
            self.assertIn(("global", "visibility"), dialog.attribute_buttons)
        finally:
            dialog.close()

    def test_no_attributes_has_clear_empty_state(self):
        from PySide6.QtWidgets import QLabel
        shape = self._polygon()
        self.win.attribute_store.replace_schema({"global": [], "classes": {}})
        dialog = self._quick_dialog(shape)
        try:
            self.assertEqual(dialog.attribute_buttons, {})
            self.assertTrue(any("尚未配置" in label.text() for label in dialog.findChildren(QLabel)))
            self.assertIn("boundary", dialog.category_button.text())
        finally:
            dialog.close()

    def test_repeated_open_and_close_releases_dialogs(self):
        from PySide6.QtCore import QCoreApplication, QEvent, QTimer
        from PySide6.QtWidgets import QApplication
        from ui.attribute_widgets import AnnotationQuickEditDialog
        shape = self._polygon()
        for _ in range(8):
            QTimer.singleShot(0, lambda: QApplication.activeModalWidget().close())
            self.win.on_shape_double_clicked(shape)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(self.win.findChildren(AnnotationQuickEditDialog), [])

    def test_double_click_while_drawing_finishes_instead_of_editing_existing_shape(self):
        from PySide6.QtCore import QPointF
        from jotmark.graphics.canvas import CanvasMode
        self._polygon()
        scene = self.win.scene
        scene.set_mode(CanvasMode.POLY)
        scene.poly_pts = [QPointF(20, 20), QPointF(30, 20), QPointF(25, 30)]
        completed, edited = [], []
        scene.shape_drawn.connect(completed.append)
        scene.shape_double_clicked.connect(edited.append)
        self._double_click(QPointF(80, 80))
        self.assertEqual(len(completed), 1)
        self.assertEqual(edited, [])
        self.assertFalse(scene.poly_pts)


if __name__ == "__main__":
    unittest.main()
