"""UI regression checks for readable class/instance-attribute overlays."""

import os
import tempfile
import unittest
from unittest.mock import patch

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for canvas label tests")
class CanvasAttributeLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings_dir = tempfile.TemporaryDirectory()
        cls.previous = {k: os.environ.get(k) for k in ("QT_QPA_PLATFORM", "XDG_CONFIG_HOME", "JOTMARK_MANUAL_ONLY")}
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
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "road.png")
        self.assertTrue(QImage(640, 480, QImage.Format_RGB32).save(self.path))
        self.win = MainWindow()
        self.win.show()
        self.win.scene.load_image(self.path)
        self.win.view.reset_to_fit()
        self.win.add_class_to_list("boundary")
        self.win.add_class_to_list("other")
        self.win.attribute_store.replace_schema({
            "global": [
                {"id": "visibility", "name": "visibility", "type": "multi_select", "options": ["visible", "partly_truncated"]},
                {"id": "review", "name": "review", "type": "single_select"},
                {"id": "bool", "name": "核查", "type": "boolean"},
                {"id": "parent", "name": "选项", "type": "single_select", "children": [
                    {"id": "child", "name": "说明", "type": "text"},
                ]},
            ],
            "classes": {"boundary": [
                {"id": "kind", "name": "boundary_kind", "type": "single_select"},
                {"id": "note", "name": "备忘", "type": "text"},
                {"id": "count", "name": "数量", "type": "number"},
            ]},
        })

    def tearDown(self):
        self.win.close()
        self.tmp.cleanup()

    def _shape(self, label="boundary", x=2):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        from jotmark.graphics.shapes import PolyShape
        shape = PolyShape(QPolygonF([QPointF(x, 5), QPointF(x + 4, 5), QPointF(x + 3, 12)]), label)
        self.win.scene.addItem(shape)
        return shape

    def _label_target_point(self, shape, target):
        from PySide6.QtCore import QPointF
        self.app.processEvents()
        label = shape.label_text
        # Find an actual painted line for this target rather than depending on
        # font metrics or an assumed zoom level on the test platform.
        ys = [y for y in range(2, int(label.boundingRect().height()) - 1)
              if label.target_at(QPointF(10, y)) == target]
        self.assertTrue(ys, (target, label.toPlainText()))
        point = label.deviceTransform(self.win.view.viewportTransform()).map(
            QPointF(10, (ys[0] + ys[-1]) / 2)).toPoint()
        self.assertTrue(self.win.view.viewport().rect().contains(point), point)
        return point

    def _click_label_target(self, shape, target):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        self.win.scene.set_mode(CanvasMode.EDIT)
        point = self._label_target_point(shape, target)
        QTest.mouseClick(self.win.view.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        self.app.processEvents()

    def test_class_first_scoped_markers_wrapping_and_complete_tooltip(self):
        from jotmark.data.exporter import Exporter
        from jotmark.graphics.shapes import AnnotationLabelItem
        shape = self._shape()
        shape.attributes = {
            "visibility": ["visible", "partly_truncated"],
            "review": "need_review", "bool": False, "child": "<invisible>",
            "kind": "road_edge", "note": "修复区域及其极长描述" * 12, "count": 0,
        }
        before = Exporter.extract_shapes(self.win.scene)
        self.win.update_annotation_tree()
        label = shape.label_text
        self.assertIsInstance(label, AnnotationLabelItem)
        plain = label.toPlainText()
        self.assertTrue(plain.startswith("boundary"), plain)
        self.assertIn("◇ visibility：visible、", plain)
        self.assertIn("partly_truncated", plain)
        self.assertIn("类别属性 · boundary_kind：road_edge", label.toolTip())
        self.assertIn("◇ 核查：否", plain)
        self.assertNotIn("说明", plain)  # Child of an unselected parent is hidden.
        self.assertIn("另有", plain)
        self.assertIn("◆", plain)
        self.assertNotIn("<invisible>", label.toolTip())
        self.assertLess(label.boundingRect().width(), 235)
        self.assertEqual(Exporter.extract_shapes(self.win.scene), before)

    def test_attribute_edits_schema_rename_and_category_change_update_immediately(self):
        shape = self._shape()
        shape.attributes = {"kind": "road_edge", "review": "normal"}
        self.win.update_annotation_tree()
        self.assertIn("◆ boundary_kind：road_edge", shape.label_text.toPlainText())
        shape.attributes["kind"] = "curb"
        self.win.on_shape_attributes_changed(shape)
        self.assertIn("◆ boundary_kind：curb", shape.label_text.toPlainText())
        with patch("main.QInputDialog.getText", return_value=("边界类型", True)):
            self.win.rename_attribute_label(shape, "kind", "class")
        self.assertIn("◆ 边界类型：curb", shape.label_text.toPlainText())
        self.win.on_shape_class_reassigned(shape, "other")
        self.assertEqual(shape.label_text.toPlainText(), "other")
        self.assertEqual(shape.attributes, {})

    def test_narrow_shape_and_zoom_keep_label_readable_and_in_view(self):
        from PySide6.QtCore import QPointF
        shape = self._shape(x=630)
        shape.attributes = {"kind": "curb"}
        self.win.update_annotation_tree()
        label = shape.label_text
        view = self.win.view
        before = label.deviceTransform(view.viewportTransform()).mapRect(label.boundingRect())
        self.assertTrue(before.intersects(view.viewport().rect()))
        self.assertGreater(before.width(), 25)
        view._zoom_at(1.7, view.mapFromScene(QPointF(630, 8)))
        self.app.processEvents()
        after = label.deviceTransform(view.viewportTransform()).mapRect(label.boundingRect())
        self.assertGreater(after.width(), 25)
        self.assertAlmostEqual(after.width(), before.width(), delta=3)
        self.assertIn("boundary", label.toPlainText())

    def test_panning_does_not_leave_ghost_labels_from_offscreen_shapes(self):
        from PySide6.QtCore import QPointF, QRectF
        from jotmark.graphics.shapes import RectShape
        left = self._shape(x=2)
        right = RectShape(QRectF(615, 410, 10, 10), "other")
        self.win.scene.addItem(right)
        self.win.update_annotation_tree()
        view = self.win.view
        view._zoom_at(6, view.mapFromScene(QPointF(320, 240)))
        view.centerOn(QPointF(620, 410))
        self.app.processEvents()
        viewport = view.viewport().rect()
        self.assertFalse(left.label_text.deviceTransform(view.viewportTransform()).mapRect(
            left.label_text.boundingRect()).intersects(viewport))
        self.assertTrue(right.label_text.deviceTransform(view.viewportTransform()).mapRect(
            right.label_text.boundingRect()).intersects(viewport))

        # Moving the scrollbars must update the edge-anchored labels too.
        view.centerOn(QPointF(5, 8))
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 1)
        self.app.processEvents()
        self.assertFalse(right.label_text.deviceTransform(view.viewportTransform()).mapRect(
            right.label_text.boundingRect()).intersects(viewport))
        self.assertTrue(left.label_text.deviceTransform(view.viewportTransform()).mapRect(
            left.label_text.boundingRect()).intersects(viewport))

    def test_schema_replacement_updates_names_and_conditional_children(self):
        shape = self._shape()
        shape.attributes = {"parent": "active", "child": "已处理"}
        self.win.update_annotation_tree()
        self.assertIn("◇ 说明：已处理", shape.label_text.toPlainText())
        self.win.attribute_store.replace_schema({
            "global": [{"id": "parent", "name": "父项", "type": "text", "children": [
                {"id": "child", "name": "新名称", "type": "text"}
            ]}], "classes": {},
        })
        self.win.update_annotation_tree()
        self.assertIn("◇ 新名称：已处理", shape.label_text.toPlainText())
        self.assertNotIn("说明", shape.label_text.toPlainText())
        shape.attributes.pop("parent")
        self.win.on_shape_attributes_changed(shape)
        self.assertNotIn("新名称", shape.label_text.toPlainText())

    def test_visibility_toggle_and_reload_json_without_mutating_values(self):
        from jotmark.data.exporter import Exporter
        shape = self._shape()
        shape.attributes = {"kind": "curb"}
        self.win.update_annotation_tree()
        self.win.scene.show_canvas_attributes = False
        self.win.update_annotation_tree()
        self.assertEqual(shape.label_text.toPlainText(), "boundary")
        source = Exporter.extract_shapes(self.win.scene)
        json_path = os.path.join(self.tmp.name, "road.json")
        Exporter.save_json(json_path, self.path, 640, 480, source)
        self.win.scene.clear_shapes()
        self.win.scene.show_canvas_attributes = True
        self.win._load_json(json_path)
        self.win.update_annotation_tree()
        restored = [s for s in self.win.scene.items() if getattr(s, "label", None) == "boundary"]
        self.assertEqual(len(restored), 1)
        self.assertIn("◆ boundary_kind：curb", restored[0].label_text.toPlainText())
        self.assertEqual(Exporter.extract_shapes(self.win.scene), source)

    def test_all_annotation_geometries_receive_scoped_labels(self):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPolygonF
        from jotmark.graphics.shapes import RectShape, PolylineShape, PointShape, RotatedRectShape, PoseShape
        shapes = [
            RectShape(QRectF(12, 20, 15, 10), "boundary"),
            PolylineShape(QPolygonF([QPointF(70, 50), QPointF(90, 60)]), "boundary"),
            PointShape(QPointF(120, 90), "boundary"),
            RotatedRectShape(180, 150, 20, 25, 30, "boundary"),
            PoseShape(QRectF(260, 100, 30, 40), {"name": "empty", "keypoints": [], "connections": []}, "boundary"),
        ]
        for shape in shapes:
            shape.attributes = {"kind": "road_edge"}
            self.win.scene.addItem(shape)
        self.win.update_annotation_tree()
        for shape in shapes:
            self.assertIn("◆ boundary_kind：road_edge", shape.label_text.toPlainText())

    def test_html_values_are_escaped_without_losing_original_label(self):
        shape = self._shape()
        shape.attributes = {"kind": "<curb> & 'road'"}
        self.win.update_annotation_tree()
        self.assertIn("<curb> & 'road'", shape.label_text.toPlainText())
        html = shape.label_text.toHtml()
        self.assertNotIn("<curb>", html)
        self.assertIn("&lt;curb&gt;", html)

    def test_long_class_name_stays_first_and_is_complete_in_tooltip(self):
        long_name = "boundary_" + "very_long_category_name_" * 8
        shape = self._shape(label=long_name)
        self.win.update_annotation_tree()
        self.assertTrue(shape.label_text.toPlainText().startswith("boundary_"))
        self.assertLess(shape.label_text.boundingRect().width(), 235)
        self.assertIn("…", shape.label_text.toPlainText())
        self.assertIn(long_name, shape.label_text.toolTip())

    def test_click_attribute_opens_picker_and_syncs_with_right_editor(self):
        from PySide6.QtWidgets import QDialog
        shape = self._shape(x=300)
        other = self._shape(x=400)
        shape.attributes = {"visibility": ["visible"]}
        other.attributes = {"visibility": ["visible"]}
        self.win.update_annotation_tree()
        with patch("ui.attribute_widgets.AttributeValuePickerDialog.exec", return_value=QDialog.Accepted), \
             patch("ui.attribute_widgets.AttributeValuePickerDialog.value", return_value=["partly_truncated"]):
            self._click_label_target(shape, ("global", "visibility"))
        self.assertTrue(shape.isSelected())
        self.assertIs(self.win.attributeEditor._shape, shape)
        self.assertEqual(shape.attributes["visibility"], ["partly_truncated"])
        self.assertEqual(other.attributes["visibility"], ["visible"])
        self.assertIn("partly_truncated", shape.label_text.toPlainText())

    def test_click_attribute_after_zoom_and_click_category_remains_reclassification(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtWidgets import QDialog
        shape = self._shape(x=300)
        shape.attributes = {"visibility": ["visible"]}
        self.win.update_annotation_tree()
        self.win.view._zoom_at(2.4, self.win.view.mapFromScene(QPointF(300, 5)))
        self.app.processEvents()
        with patch("ui.attribute_widgets.AttributeValuePickerDialog.exec", return_value=QDialog.Rejected) as picker:
            self._click_label_target(shape, ("global", "visibility"))
        picker.assert_called_once()
        self.assertEqual(shape.attributes["visibility"], ["visible"])
        with patch.object(self.win, "edit_shape_label") as edit_class:
            self._click_label_target(shape, ("category", ""))
        edit_class.assert_called_once_with(shape)

    def test_canvas_boolean_text_number_and_parent_clear_use_existing_rules(self):
        shape = self._shape(x=300)
        shape.attributes = {"bool": False, "parent": "active", "child": "child value", "count": 0}
        shape.setSelected(True)
        self.win.update_attribute_editor()
        with patch("ui.attribute_widgets.QInputDialog.getItem", return_value=("是", True)):
            self.win.on_canvas_attribute_clicked(shape, "global", "bool")
        self.assertIs(shape.attributes["bool"], True)
        with patch("ui.attribute_widgets.QInputDialog.getText", return_value=("12.5", True)):
            self.win.on_canvas_attribute_clicked(shape, "class", "count")
        self.assertEqual(shape.attributes["count"], 12.5)
        with patch("ui.attribute_widgets.QInputDialog.getText", return_value=("not-a-number", True)), \
             patch("ui.attribute_widgets.QMessageBox.warning") as warning:
            self.win.on_canvas_attribute_clicked(shape, "class", "count")
        warning.assert_called_once()
        self.assertEqual(shape.attributes["count"], 12.5)
        with patch("ui.attribute_widgets.QInputDialog.getText", return_value=("", True)):
            self.win.on_canvas_attribute_clicked(shape, "global", "parent")
        self.assertNotIn("parent", shape.attributes)
        self.assertNotIn("child", shape.attributes)

    def test_click_summary_opens_attribute_scopes(self):
        shape = self._shape(x=300)
        shape.attributes = {"visibility": ["visible"], "review": "normal", "bool": False,
                            "parent": "active", "child": "done", "kind": "curb"}
        self.win.update_annotation_tree()
        with patch.object(self.win.attributeEditor, "open_all_attribute_scopes") as open_scopes:
            self._click_label_target(shape, ("summary", ""))
        open_scopes.assert_called_once()

    def test_wrapped_lines_with_unicode_and_spaces_hit_the_correct_attribute(self):
        from PySide6.QtCore import QPointF
        shape = self._shape(label="类别😀" * 20, x=300)
        shape.attributes = {"visibility": ["可见   特征" * 18], "bool": False}
        self.win.update_annotation_tree()
        label = shape.label_text
        found = [label.target_at(QPointF(10, y)) for y in range(3, int(label.boundingRect().height()) - 1)]
        self.assertIn(("category", ""), found)
        self.assertIn(("global", "visibility"), found)
        self.assertIn(("global", "bool"), found)
        self.assertGreater(sum(target == ("global", "visibility") for target in found), 20)
        self.assertLess(found.index(("global", "visibility")), found.index(("global", "bool")))

    def test_canvas_edit_is_saved_and_can_be_undone_and_redone(self):
        import json
        from PySide6.QtWidgets import QDialog
        from jotmark.data.exporter import Exporter
        shape = self._shape(x=300)
        shape.attributes = {"visibility": ["visible"]}
        self.win.current_image_path = self.path
        self.win.current_format = "json"
        self.win.update_annotation_tree()
        self.win.push_state()
        with patch("ui.attribute_widgets.AttributeValuePickerDialog.exec", return_value=QDialog.Accepted), \
             patch("ui.attribute_widgets.AttributeValuePickerDialog.value", return_value=["partly_truncated"]):
            self._click_label_target(shape, ("global", "visibility"))
        self.assertEqual(Exporter.extract_shapes(self.win.scene)[0]["attributes"]["visibility"], ["partly_truncated"])
        self.win.auto_save_annotation(immediate=True)
        with open(os.path.join(self.tmp.name, "road.json"), encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["shapes"][0]["attributes"]["visibility"], ["partly_truncated"])
        self.win.undo()
        self.assertEqual(Exporter.extract_shapes(self.win.scene)[0]["attributes"]["visibility"], ["visible"])
        self.win.redo()
        self.assertEqual(Exporter.extract_shapes(self.win.scene)[0]["attributes"]["visibility"], ["partly_truncated"])

    def test_summary_scope_selection_and_boolean_clear(self):
        shape = self._shape(x=300)
        shape.attributes = {"bool": False}
        shape.setSelected(True)
        self.win.update_attribute_editor()
        with patch("ui.attribute_widgets.QInputDialog.getItem", return_value=("类别属性（仅当前类别）", True)), \
             patch.object(self.win.attributeEditor, "_open_scope_dialog") as open_scope:
            self.win.on_canvas_attributes_summary_clicked(shape)
        open_scope.assert_called_once_with("class", "类别属性（仅当前类别）")
        with patch("ui.attribute_widgets.QInputDialog.getItem", return_value=("清除", True)):
            self.win.on_canvas_attribute_clicked(shape, "global", "bool")
        self.assertNotIn("bool", shape.attributes)

    def test_single_picker_preselects_and_clears_current_value(self):
        from ui.attribute_widgets import AttributeValuePickerDialog
        picker = AttributeValuePickerDialog("类型", ["road_edge", "curb"], "curb")
        self.assertEqual(picker.value(), "curb")
        picker._clear()
        self.assertEqual(picker.value(), "")
        multi = AttributeValuePickerDialog("状态", ["a", "b"], ["a", "b"], multiple=True)
        self.assertEqual(multi.value(), ["a", "b"])
        multi._clear()
        self.assertEqual(multi.value(), [])

    def test_canvas_click_uses_real_single_value_picker(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from ui.attribute_widgets import AttributeValuePickerDialog
        schema = self.win.attribute_store.attributes_for("boundary")
        schema[0]["options"] = ["road_edge", "curb"]
        self.win.attribute_store.set_attributes("boundary", schema)
        shape = self._shape(x=300)
        shape.attributes = {"kind": "road_edge"}
        self.win.update_annotation_tree()
        seen = []

        def select_curb():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, AttributeValuePickerDialog):
                seen.append(dialog.value())
                dialog.list_widget.setCurrentRow(1)
                dialog.accept()
            elif dialog:
                dialog.reject()

        QTimer.singleShot(50, select_curb)
        self._click_label_target(shape, ("class", "kind"))
        self.assertEqual(seen, ["road_edge"])
        self.assertEqual(shape.attributes["kind"], "curb")

    def test_label_click_does_not_open_picker_while_drawing_polygon(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        shape = self._shape(x=300)
        shape.attributes = {"visibility": ["visible"]}
        self.win.update_annotation_tree()
        self.win.scene.set_mode(CanvasMode.POLY)
        self.win.scene.poly_pts.append(QPointF(200, 200))
        point = self._label_target_point(shape, ("global", "visibility"))
        with patch.object(self.win.attributeEditor, "edit_attribute_value") as edit_attribute:
            QTest.mouseClick(self.win.view.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            self.app.processEvents()
        edit_attribute.assert_not_called()
        self.assertEqual(len(self.win.scene.poly_pts), 2)


if __name__ == "__main__":
    unittest.main()
