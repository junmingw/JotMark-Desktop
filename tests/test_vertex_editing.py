"""Regression checks for vertex dragging, release, deletion and persistence."""

import json
import os
import tempfile
import unittest

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for vertex editing tests")
class VertexEditingTests(unittest.TestCase):
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
        self.win.attribute_store.load(self.directory.name)
        self.win.add_class_to_list("boundary")
        self.win.scene.load_image(self.image)
        self.win.view.reset_to_fit()
        self.win.scene.set_mode(CanvasMode.EDIT)
        self.app.processEvents()

    def tearDown(self):
        self.win._autosave_timer.stop()
        self.win._autosave_dirty = False
        self.win.close()
        self.app.processEvents()
        self.directory.cleanup()

    def _shape(self, line=False):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        from jotmark.graphics.shapes import PolyShape, PolylineShape
        points = QPolygonF([QPointF(45, 55), QPointF(155, 55), QPointF(155, 130), QPointF(45, 130)])
        shape = (PolylineShape if line else PolyShape)(points, "boundary")
        self.win.scene.addItem(shape)
        shape.setSelected(True)
        self.app.processEvents()
        return shape

    def _point(self, shape, index):
        return self.win.view.mapFromScene(shape.mapToScene(shape.polygon()[index]))

    def _drag(self, shape, index, move, release):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        viewport = self.win.view.viewport()
        start = self._point(shape, index)
        QTest.mousePress(viewport, Qt.LeftButton, pos=start)
        if move is not None:
            mid = self.win.view.mapFromScene(move)
            mouse_move = QMouseEvent(QEvent.MouseMove, QPointF(mid), QPointF(viewport.mapToGlobal(mid)),
                                     Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            self.app.sendEvent(viewport, mouse_move)
        end = self.win.view.mapFromScene(release)
        QTest.mouseRelease(viewport, Qt.LeftButton, pos=end)
        self.app.processEvents()

    def test_polygon_and_polyline_drag_keep_vertices_at_final_release(self):
        from PySide6.QtCore import QPointF
        for line in (False, True):
            with self.subTest(line=line):
                shape = self._shape(line)
                self._drag(shape, 0, QPointF(65, 70), QPointF(80, 80))
                self.assertEqual(shape.polygon().count(), 4)
                self.assertAlmostEqual(shape.polygon()[0].x(), 80, delta=1)
                self.assertAlmostEqual(shape.polygon()[0].y(), 80, delta=1)
                self.win.scene.removeItem(shape)

    def test_small_and_large_vertex_handles_remain_draggable(self):
        from PySide6.QtCore import QPointF
        for diameter in (2, 10.5):
            for line in (False, True):
                with self.subTest(diameter=diameter, line=line):
                    self.win.scene.set_vertex_handle_diameter(diameter)
                    shape = self._shape(line)
                    self.assertEqual(shape.handles[0].boundingRect().width(), diameter)
                    self._drag(shape, 0, QPointF(65, 70), QPointF(80, 80))
                    self.assertAlmostEqual(shape.polygon()[0].x(), 80, delta=1)
                    self.assertAlmostEqual(shape.polygon()[0].y(), 80, delta=1)
                    self.win.scene.removeItem(shape)

    def test_in_progress_vertices_follow_undo_and_redo(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        self.win._set_mode(CanvasMode.POLY)
        viewport = self.win.view.viewport()
        for point in (QPointF(30, 30), QPointF(100, 30), QPointF(100, 100)):
            QTest.mouseClick(viewport, Qt.LeftButton, pos=self.win.view.mapFromScene(point))
        expected = list(self.win.scene.poly_pts)
        self.assertEqual(len(self.win.scene._drawing_vertex_markers), 3)
        self.win.undo()
        self.assertEqual(self.win.scene.poly_pts, expected[:2])
        self.assertEqual(len(self.win.scene._drawing_vertex_markers), 2)
        self.win.redo()
        self.assertEqual(self.win.scene.poly_pts, expected)
        self.assertEqual(len(self.win.scene._drawing_vertex_markers), 3)

    def test_image_switch_confirms_before_discarding_draft_and_clears_redo(self):
        from unittest.mock import patch
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QImage
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QMessageBox
        from jotmark.graphics.canvas import CanvasMode
        next_image = os.path.join(self.directory.name, "next.png")
        self.assertTrue(QImage(240, 180, QImage.Format_RGB32).save(next_image))
        first = self.win._create_file_list_item(self.image)
        second = self.win._create_file_list_item(next_image)
        blocked = self.win.listFiles.blockSignals(True)
        self.win.listFiles.addItem(first)
        self.win.listFiles.addItem(second)
        self.win.listFiles.setCurrentItem(first)
        self.win.listFiles.blockSignals(blocked)
        self.win._set_mode(CanvasMode.POLY)
        for point in (QPointF(30, 30), QPointF(100, 30), QPointF(100, 100)):
            QTest.mouseClick(self.win.view.viewport(), Qt.LeftButton, pos=self.win.view.mapFromScene(point))
        self.win.undo()
        before = list(self.win.scene.poly_pts)
        with patch("main.QMessageBox.question", return_value=QMessageBox.No):
            self.win.listFiles.setCurrentItem(second)
        self.assertEqual(self.win.current_image_path, self.image)
        self.assertEqual(self.win.scene.poly_pts, before)
        self.assertIs(self.win.listFiles.currentItem(), first)
        self.assertTrue(self.win._poly_redo_pts)
        with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
            self.win.listFiles.setCurrentItem(second)
        self.assertEqual(self.win.current_image_path, next_image)
        self.assertFalse(self.win.scene.poly_pts)
        self.assertFalse(self.win.scene._drawing_vertex_markers)
        self.assertFalse(self.win._poly_redo_pts)

    def test_cancel_directory_switch_preserves_draft_and_project(self):
        from unittest.mock import patch
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QMessageBox
        from jotmark.graphics.canvas import CanvasMode
        self.win._set_mode(CanvasMode.POLY)
        QTest.mouseClick(self.win.view.viewport(), Qt.LeftButton,
                         pos=self.win.view.mapFromScene(QPointF(30, 30)))
        points = list(self.win.scene.poly_pts)
        with tempfile.TemporaryDirectory() as target, \
             patch("main.QFileDialog.getExistingDirectory", return_value=target), \
             patch("main.QMessageBox.question", return_value=QMessageBox.No):
            self.win.open_dir()
        self.assertEqual(self.win.current_dir, self.directory.name)
        self.assertEqual(self.win.current_image_path, self.image)
        self.assertEqual(self.win.scene.poly_pts, points)
        self.assertEqual(len(self.win.scene._drawing_vertex_markers), 1)

    def test_release_without_final_move_and_click_do_not_delete_vertex(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        shape = self._shape()
        QTest.mouseClick(self.win.view.viewport(), Qt.LeftButton, pos=self._point(shape, 0))
        self.assertEqual(shape.polygon().count(), 4)
        self._drag(shape, 0, None, QPointF(75, 85))
        self.assertEqual(shape.polygon().count(), 4)
        self.assertAlmostEqual(shape.polygon()[0].x(), 75, delta=1)
        self.assertAlmostEqual(shape.polygon()[0].y(), 85, delta=1)

    def test_vertex_drag_persists_and_supports_undo_redo(self):
        from PySide6.QtCore import QPointF
        from jotmark.graphics.shapes import PolyShape
        shape = self._shape()
        shape.attributes = {"note": "keep"}
        self.win.push_state()
        self._drag(shape, 0, QPointF(60, 70), QPointF(80, 85))
        self.assertTrue(self.win._flush_auto_save_annotation())
        with open(os.path.join(self.directory.name, "road.json"), encoding="utf-8") as stream:
            data = json.load(stream)
        self.assertEqual(len(data["shapes"][0]["points"]), 4)
        self.assertAlmostEqual(data["shapes"][0]["points"][0][0], 80, delta=1)
        self.assertEqual(data["shapes"][0]["attributes"], {"note": "keep"})
        self.win.undo()
        restored = next(item for item in self.win.scene.items() if isinstance(item, PolyShape))
        self.assertEqual(restored.polygon().count(), 4)
        self.assertAlmostEqual(restored.polygon()[0].x(), 45, delta=1)
        self.win.redo()
        restored = next(item for item in self.win.scene.items() if isinstance(item, PolyShape))
        self.assertAlmostEqual(restored.polygon()[0].x(), 80, delta=1)

    def test_vertex_drag_with_sam_enabled_still_saves(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QPointF
        shape = self._shape()
        self.win.scene.sam_client = SimpleNamespace(current_model_key="sam3")
        self.win.scene.sam_enabled = True
        self._drag(shape, 0, QPointF(65, 70), QPointF(80, 85))
        self.assertTrue(self.win._autosave_dirty)
        self.assertTrue(self.win._flush_auto_save_annotation())
        with open(os.path.join(self.directory.name, "road.json"), encoding="utf-8") as stream:
            saved = json.load(stream)
        self.assertEqual(len(saved["shapes"][0]["points"]), 4)
        self.assertAlmostEqual(saved["shapes"][0]["points"][0][0], 80, delta=1)

    def test_zoomed_translated_vertex_drag_is_clamped_to_image(self):
        from PySide6.QtCore import QPointF
        shape = self._shape()
        shape.setPos(7, 4)
        self.win.view._zoom_at(1.4, self.win.view.mapFromScene(QPointF(50, 60)))
        self.app.processEvents()
        self._drag(shape, 0, QPointF(15, 15), QPointF(-10, 15))
        point = shape.mapToScene(shape.polygon()[0])
        self.assertEqual(shape.polygon().count(), 4)
        self.assertAlmostEqual(point.x(), 0, delta=1)
        self.assertAlmostEqual(point.y(), 15, delta=1)

    def test_rectangle_resize_handles_still_work(self):
        from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        from jotmark.graphics.shapes import RectShape
        shape = RectShape(QRectF(45, 55, 110, 75), "boundary")
        self.win.scene.addItem(shape)
        shape.setSelected(True)
        viewport = self.win.view.viewport()
        start = self.win.view.mapFromScene(QPointF(155, 130))
        end = self.win.view.mapFromScene(QPointF(185, 150))
        QTest.mousePress(viewport, Qt.LeftButton, pos=start)
        event = QMouseEvent(QEvent.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
                            Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        self.app.sendEvent(viewport, event)
        QTest.mouseRelease(viewport, Qt.LeftButton, pos=end)
        self.assertAlmostEqual(shape.rect().right(), 185, delta=1)
        self.assertAlmostEqual(shape.rect().bottom(), 150, delta=1)

    def test_inserted_vertex_uses_final_release_position(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        for line in (False, True):
            with self.subTest(line=line):
                shape = self._shape(line)
                viewport = self.win.view.viewport()
                start = self.win.view.mapFromScene(QPointF(155, 95))
                end = self.win.view.mapFromScene(QPointF(180, 100))
                QTest.mouseMove(viewport, viewport.rect().topLeft())
                QTest.mouseMove(viewport, start)
                self.app.processEvents()
                self.assertNotEqual(shape.ghost_idx, -1)
                QTest.mousePress(viewport, Qt.LeftButton, pos=start)
                QTest.mouseRelease(viewport, Qt.LeftButton, pos=end)
                self.app.processEvents()
                self.assertEqual(shape.polygon().count(), 5)
                self.assertAlmostEqual(shape.polygon()[2].x(), 180, delta=1)
                self.assertAlmostEqual(shape.polygon()[2].y(), 100, delta=1)
                self.win.scene.removeItem(shape)

    def test_vertex_context_menu_explicit_delete(self):
        from PySide6.QtCore import QPointF, QTimer, Qt
        from PySide6.QtGui import QContextMenuEvent
        from PySide6.QtTest import QTest
        from jotmark.graphics.shapes import PolyShape
        shape = self._shape()
        self.win.push_state()
        viewport = self.win.view.viewport()
        pos = self._point(shape, 0)
        event = QContextMenuEvent(QContextMenuEvent.Mouse, pos, viewport.mapToGlobal(pos))
        menus = []
        def select_menu_action():
            popup = self.app.activePopupWidget()
            if popup:
                menus.append([action.text() for action in popup.actions()])
                popup.setActiveAction(popup.actions()[0])
                QTest.keyClick(popup, Qt.Key_Return)
        QTimer.singleShot(100, select_menu_action)
        self.app.sendEvent(viewport, event)
        self.app.processEvents()
        self.assertEqual(shape.polygon().count(), 3, menus)
        self.assertEqual(shape.polygon()[0], QPointF(155, 55))
        self.assertTrue(self.win._autosave_dirty)
        self.win.undo()
        restored = next(item for item in self.win.scene.items() if isinstance(item, PolyShape))
        self.assertEqual(restored.polygon().count(), 4)
        self.win.redo()
        restored = next(item for item in self.win.scene.items() if isinstance(item, PolyShape))
        self.assertEqual(restored.polygon().count(), 3)

    def test_vertex_delete_is_disabled_at_minimum_count(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QContextMenuEvent, QPolygonF
        for line, minimum in ((False, 3), (True, 2)):
            with self.subTest(line=line):
                shape = self._shape(line)
                shape.setPolygon(QPolygonF(list(shape.polygon())[:minimum]))
                viewport = self.win.view.viewport()
                pos = self._point(shape, 0)
                event = QContextMenuEvent(QContextMenuEvent.Mouse, pos, viewport.mapToGlobal(pos))
                enabled = []
                def close_menu():
                    popup = self.app.activePopupWidget()
                    if popup:
                        enabled.append(popup.actions()[0].isEnabled())
                        popup.close()
                QTimer.singleShot(100, close_menu)
                self.app.sendEvent(viewport, event)
                self.assertEqual(enabled, [False])
                self.assertEqual(shape.polygon().count(), minimum)
                self.win.scene.removeItem(shape)

    def test_context_delete_last_vertex_can_continue_editing(self):
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtGui import QContextMenuEvent
        from PySide6.QtTest import QTest

        for line in (False, True):
            with self.subTest(line=line):
                shape = self._shape(line)
                viewport = self.win.view.viewport()
                pos = self._point(shape, len(shape.handles) - 1)
                event = QContextMenuEvent(QContextMenuEvent.Mouse, pos, viewport.mapToGlobal(pos))

                def select_action():
                    popup = self.app.activePopupWidget()
                    if popup:
                        popup.setActiveAction(popup.actions()[0])
                        QTest.keyClick(popup, Qt.Key_Return)

                QTimer.singleShot(100, select_action)
                self.app.sendEvent(viewport, event)
                self.app.processEvents()
                self.assertEqual(shape.polygon().count(), 3)
                shape.setSelected(False)
                shape.setSelected(True)
                self.app.processEvents()
                self.assertEqual(len(shape.handles), 3)
                self.win.scene.removeItem(shape)
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
