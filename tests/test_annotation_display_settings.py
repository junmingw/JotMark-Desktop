"""Offscreen regression coverage for drawing preferences (no image files)."""

import os
import tempfile
import unittest
from unittest.mock import patch

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for the offscreen UI checks")
class AnnotationDisplaySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_dir = tempfile.TemporaryDirectory()
        cls.previous = {key: os.environ.get(key) for key in ("QT_QPA_PLATFORM", "XDG_CONFIG_HOME", "JOTMARK_MANUAL_ONLY")}
        os.environ.update({
            "QT_QPA_PLATFORM": "offscreen",
            "XDG_CONFIG_HOME": cls.config_dir.name,
            "JOTMARK_MANUAL_ONLY": "1",
        })
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.config_dir.cleanup()
        for key, value in cls.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        from main import MainWindow
        self.window = MainWindow()
        self.window.add_class_to_list("person")
        self.window.add_class_to_list("car")

    def tearDown(self):
        self.window.close()

    def test_consecutive_geometry_inherits_last_class_and_width(self):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPolygonF
        from jotmark.data.exporter import Exporter
        from jotmark.graphics.shapes import RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape
        win = self.window
        crosshair = win.scene.h_line
        win.scene.set_annotation_line_width(0.5)
        self.assertIs(win.scene.h_line, crosshair)
        win.classListWidget.preferred_class = "car"
        shapes = [
            RectShape(QRectF(10, 10, 20, 20)),
            PolyShape(QPolygonF([QPointF(40, 40), QPointF(60, 40), QPointF(50, 60)])),
            PolylineShape(QPolygonF([QPointF(70, 70), QPointF(90, 90)])),
            PointShape(QPointF(100, 100)),
            RotatedRectShape(110, 110, 20, 30, 0),
        ]
        for shape in shapes:
            win.handle_new_shape(shape)
            self.assertEqual(shape.label, "car")
            pen = shape.rect_item.pen() if hasattr(shape, "rect_item") else shape.pen()
            self.assertEqual(pen.widthF(), 0.5)

        shapes[1].attributes = {"color": "red"}
        before = Exporter.extract_shapes(win.scene)
        undo_size = len(win.undo_stack)
        win.scene.set_annotation_line_width(1.5)
        self.assertIs(win.scene.h_line, crosshair)
        self.assertTrue(win.scene.items())
        for shape in shapes:
            pen = shape.rect_item.pen() if hasattr(shape, "rect_item") else shape.pen()
            self.assertEqual(pen.widthF(), 1.5)
        self.assertEqual(Exporter.extract_shapes(win.scene), before)
        self.assertEqual(len(win.undo_stack), undo_size)

    def test_tree_class_choice_overrides_last_and_survives_clear(self):
        from PySide6.QtCore import QRectF
        from jotmark.graphics.shapes import RectShape
        from jotmark.graphics.canvas import CanvasMode
        win = self.window
        win.classListWidget.preferred_class = "person"
        win._set_mode(CanvasMode.RECT)
        item = win.classListWidget._class_items["car"]
        win.classListWidget.treeWidget.setCurrentItem(item)
        self.assertEqual(win.classListWidget.get_annotation_class(), "car")
        win.handle_new_shape(RectShape(QRectF(5, 5, 30, 30)))
        win.classListWidget.clear_tree_selection()
        self.assertEqual(win.classListWidget.get_annotation_class(), "car")
        self.assertIsNone(win.classListWidget.get_selected_class())

    def test_half_pixel_pose_line_and_width_limits(self):
        from PySide6.QtCore import QRectF
        from jotmark.graphics.shapes import PoseShape
        template = {
            "name": "two points", "label": "person",
            "keypoints": [
                {"name": "a", "color": "#FF0000", "default_pos": [0.2, 0.2]},
                {"name": "b", "color": "#00FF00", "default_pos": [0.8, 0.8]},
            ],
            "connections": [[0, 1]],
        }
        pose = PoseShape(QRectF(10, 10, 40, 40), template, "person")
        scene = self.window.scene
        scene.addItem(pose)
        scene.set_annotation_line_width(0.5)
        self.assertEqual(scene.annotation_line_width, 0.5)
        self.assertEqual(pose.lines[0][0].pen().widthF(), 0.5)
        scene.set_annotation_line_width(0.1)
        self.assertEqual(pose.lines[0][0].pen().widthF(), 0.5)
        scene.set_annotation_line_width(1.5)
        self.assertEqual(pose.lines[0][0].pen().widthF(), 1.5)

    def test_grouped_settings_apply_and_persist(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QDialog
        from ui.settings_dialog import SettingsDialog
        win = self.window
        dialog = SettingsDialog(win)
        dialog.line_width.setValue(0.5)
        self.assertEqual(dialog.line_width.singleStep(), 0.5)
        dialog.canvas_attributes.setChecked(False)
        dialog.transparent_label_background.setChecked(True)
        dialog.shape_double_click_mode.setCurrentIndex(dialog.shape_double_click_mode.findData("category"))
        dialog.edge_click_margin.setValue(70)
        dialog.extensions.setChecked(True)
        dialog.sort_key.setCurrentIndex(dialog.sort_key.findData("size"))
        dialog.descending.setChecked(True)
        dialog.dark_theme.setChecked(True)
        dialog.exec = lambda: QDialog.Accepted
        with patch("main.SettingsDialog", return_value=dialog):
            win.show_settings_dialog()
        stored = QSettings("luohuabuxiema", "JotMark")
        self.assertEqual(float(stored.value("annotation_line_width")), 0.5)
        self.assertEqual(stored.value("edge_click_margin", type=int), 70)
        self.assertTrue(win.show_file_extensions)
        self.assertEqual(win.file_sort_key, "size")
        self.assertTrue(win.file_sort_descending)
        self.assertTrue(win.is_dark_theme)
        self.assertFalse(win.scene.show_canvas_attributes)
        self.assertFalse(stored.value("show_canvas_attributes", type=bool))
        self.assertTrue(stored.value("transparent_label_background", type=bool))
        self.assertTrue(win.scene.transparent_label_background)
        self.assertEqual(win.shape_double_click_mode, "category")
        self.assertEqual(stored.value("shape_double_click_mode"), "category")
        self.assertEqual(win.scene.annotation_line_width, 0.5)
        from main import MainWindow
        another = MainWindow()
        try:
            self.assertEqual(another.scene.annotation_line_width, 0.5)
            self.assertEqual(another.scene.edge_click_margin, 70)
            self.assertTrue(another.is_dark_theme)
            self.assertFalse(another.scene.show_canvas_attributes)
            self.assertTrue(another.scene.transparent_label_background)
            self.assertEqual(another.shape_double_click_mode, "category")
        finally:
            another.close()

    def test_cancel_does_not_change_settings_and_class_rename_delete(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtWidgets import QDialog
        from jotmark.graphics.shapes import RectShape
        from ui.settings_dialog import SettingsDialog
        win = self.window
        initial_width = win.scene.annotation_line_width
        dialog = SettingsDialog(win)
        dialog.line_width.setValue(12 if initial_width != 12 else 0.5)
        original_margin = win.scene.edge_click_margin
        dialog.edge_click_margin.setValue(0 if original_margin != 0 else 20)
        initial_transparency = win.scene.transparent_label_background
        dialog.transparent_label_background.setChecked(not initial_transparency)
        initial_double_click_mode = win.shape_double_click_mode
        dialog.shape_double_click_mode.setCurrentIndex(dialog.shape_double_click_mode.findData(
            "category" if initial_double_click_mode != "category" else "category_attributes"))
        dialog.exec = lambda: QDialog.Rejected
        with patch("main.SettingsDialog", return_value=dialog):
            win.show_settings_dialog()
        self.assertEqual(win.scene.annotation_line_width, initial_width)
        self.assertEqual(win.scene.edge_click_margin, original_margin)
        self.assertEqual(win.scene.transparent_label_background, initial_transparency)
        self.assertEqual(win.shape_double_click_mode, initial_double_click_mode)

        tree = win.classListWidget
        tree.preferred_class = "car"
        tree.rename_class_in_data("car", "vehicle")
        self.assertEqual(tree.get_annotation_class(), "vehicle")
        tree.clear_tree_selection()
        shape = RectShape(QRectF(10, 10, 20, 20))
        win.handle_new_shape(shape)
        self.assertEqual(shape.label, "vehicle")
        tree.remove_class("vehicle")
        self.assertIsNone(tree.preferred_class)

    def test_restore_image_directory_setting(self):
        from PySide6.QtWidgets import QPushButton
        from ui.settings_dialog import SettingsDialog
        win = self.window
        with tempfile.TemporaryDirectory() as output_dir:
            win.set_annotation_output_dir(output_dir)
            dialog = SettingsDialog(win)
            self.assertEqual(dialog.output_dir.text(), output_dir)
            for button in dialog.findChildren(QPushButton):
                if button.text() == "图片同目录":
                    button.click()
                    break
            self.assertEqual(dialog.output_dir.text(), "")
            win.set_annotation_output_dir(dialog.output_dir.text())
            self.assertEqual(win.annotation_output_dir, "")

    def test_double_click_mode_defaults_and_invalid_saved_value(self):
        from PySide6.QtCore import QSettings
        from main import MainWindow
        settings = QSettings("luohuabuxiema", "JotMark")
        settings.remove("shape_double_click_mode")
        default_window = MainWindow()
        try:
            self.assertEqual(default_window.shape_double_click_mode, "category_attributes")
        finally:
            default_window.close()
        settings.setValue("shape_double_click_mode", "unsupported")
        invalid_window = MainWindow()
        try:
            self.assertEqual(invalid_window.shape_double_click_mode, "category_attributes")
        finally:
            invalid_window.close()
            settings.remove("shape_double_click_mode")

    def test_saved_annotations_reload_with_display_width(self):
        from PySide6.QtGui import QImage
        from jotmark.data.exporter import Exporter
        win = self.window
        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "sample.png")
            annotation_path = os.path.join(directory, "sample.json")
            self.assertTrue(QImage(160, 160, QImage.Format_RGB32).save(image_path))
            win.scene.load_image(image_path)
            records = [
                {"label": "car", "type": "rectangle", "points": [[5, 5], [25, 30]], "attributes": {"id": "a"}},
                {"label": "car", "type": "polygon", "points": [[45, 45], [65, 45], [55, 65]], "attributes": {}},
                {"label": "person", "type": "linestrip", "points": [[90, 90], [110, 110]], "attributes": {}},
            ]
            Exporter.save_json(annotation_path, image_path, 160, 160, records)
            win.scene.set_annotation_line_width(6)
            win._load_json(annotation_path)
            actual = Exporter.extract_shapes(win.scene)
            self.assertEqual(sorted(actual, key=lambda shape: shape["type"]),
                             sorted(records, key=lambda shape: shape["type"]))
            for shape in win.scene.items():
                if getattr(shape, "label", "") in ("car", "person"):
                    self.assertEqual(shape.pen().widthF(), 6)


if __name__ == "__main__":
    unittest.main()
