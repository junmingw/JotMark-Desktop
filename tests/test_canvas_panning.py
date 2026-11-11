"""Real viewport events must pan without changing annotation state."""

import os
import tempfile
import unittest

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for pan interaction tests")
class CanvasPanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtGui import QImage
        from jotmark.graphics.canvas import Canvas
        from ui.main_window import CanvasView
        self.directory = tempfile.TemporaryDirectory()
        self.image = os.path.join(self.directory.name, "road.png")
        self.assertTrue(QImage(240, 120, QImage.Format_RGB32).save(self.image))
        self.view = CanvasView()
        self.view.resize(500, 400)
        self.scene = Canvas()
        self.view.setScene(self.scene)
        self.view.show()
        self.scene.load_image(self.image)
        self.view.reset_to_fit()
        self.app.processEvents()

    def tearDown(self):
        self.view.close()
        self.app.processEvents()
        self.directory.cleanup()

    def _move(self, pos, buttons, modifiers):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        viewport = self.view.viewport()
        event = QMouseEvent(QEvent.MouseMove, QPointF(pos), QPointF(viewport.mapToGlobal(pos)),
                            Qt.NoButton, buttons, modifiers)
        self.app.sendEvent(viewport, event)

    def _pan(self, button, modifiers, start, end, release_modifiers=None):
        from PySide6.QtTest import QTest
        viewport = self.view.viewport()
        QTest.mousePress(viewport, button, modifiers, start)
        self.assertTrue(self.view._is_panning)
        self._move(end, button, modifiers)
        QTest.mouseRelease(viewport, button, modifiers if release_modifiers is None else release_modifiers, end)
        self.app.processEvents()
        self.assertFalse(self.view._is_panning)

    def test_fit_view_can_pan_with_both_gestures_without_geometry_changes(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QPolygonF
        from jotmark.data.exporter import Exporter
        from jotmark.graphics.canvas import CanvasMode
        from jotmark.graphics.shapes import PolyShape
        self.scene.set_mode(CanvasMode.EDIT)
        shape = PolyShape(QPolygonF([QPointF(30, 20), QPointF(140, 20), QPointF(80, 90)]), "road")
        self.scene.addItem(shape)
        shape.setSelected(True)
        before = Exporter.extract_shapes(self.scene)
        image_rect = self.scene.sceneRect()
        for button, modifiers in ((Qt.LeftButton, Qt.AltModifier), (Qt.MiddleButton, Qt.NoModifier)):
            with self.subTest(button=button):
                self.view.reset_to_fit()
                self.assertEqual(self.view.horizontalScrollBar().maximum(), 0)
                before_pos = self.view.mapFromScene(QPointF(80, 60))
                start = self.view.mapFromScene(QPointF(80, 60))
                self._pan(button, modifiers, start, start + QPoint(30, 20), Qt.NoModifier)
                after_pos = self.view.mapFromScene(QPointF(80, 60))
                self.assertAlmostEqual(after_pos.x() - before_pos.x(), 30, delta=2)
                self.assertAlmostEqual(after_pos.y() - before_pos.y(), 20, delta=2)
                self.assertTrue(shape.isSelected())
                self.assertEqual(Exporter.extract_shapes(self.scene), before)
                self.assertEqual(self.scene.sceneRect(), image_rect)
                self.assertEqual(self.view.viewport().cursor().shape(), Qt.CrossCursor)

    def test_pan_during_polygon_and_line_preserves_in_progress_points(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        viewport = self.view.viewport()
        for mode in (CanvasMode.POLY, CanvasMode.LINE):
            for button, modifiers in ((Qt.LeftButton, Qt.AltModifier), (Qt.MiddleButton, Qt.NoModifier)):
                with self.subTest(mode=mode, button=button):
                    self.view.reset_to_fit()
                    self.scene.set_mode(mode)
                    for pt in (QPointF(30, 30), QPointF(70, 35)):
                        QTest.mouseClick(viewport, Qt.LeftButton, pos=self.view.mapFromScene(pt))
                    points = list(self.scene.poly_pts)
                    self.assertEqual(len(points), 2)
                    self.assertEqual(len(self.scene._drawing_vertex_markers), 2)
                    drawn = []
                    def record(shape):
                        drawn.append(shape)
                    self.scene.shape_drawn.connect(record)
                    start = self.view.mapFromScene(QPointF(70, 35))
                    self._pan(button, modifiers, start, start + QPoint(20, 15))
                    self.assertEqual(self.scene.poly_pts, points)
                    self.assertEqual(drawn, [])
                    self.assertEqual([m.pos() for m in self.scene._drawing_vertex_markers], points)
                    QTest.mouseClick(viewport, Qt.LeftButton, pos=self.view.mapFromScene(QPointF(110, 65)))
                    self.assertEqual(len(self.scene.poly_pts), 3)
                    self.scene.shape_drawn.disconnect(record)

    def test_alt_click_and_alt_double_click_do_not_add_or_finish_points(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        self.scene.set_mode(CanvasMode.POLY)
        viewport = self.view.viewport()
        for pt in (QPointF(30, 30), QPointF(90, 30), QPointF(90, 75)):
            QTest.mouseClick(viewport, Qt.LeftButton, pos=self.view.mapFromScene(pt))
        before = list(self.scene.poly_pts)
        QTest.mouseClick(viewport, Qt.LeftButton, Qt.AltModifier, self.view.mapFromScene(QPointF(110, 80)))
        QTest.mouseDClick(viewport, Qt.LeftButton, Qt.AltModifier, self.view.mapFromScene(QPointF(110, 80)))
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.AltModifier, self.view.mapFromScene(QPointF(110, 80)))
        self.assertEqual(self.scene.poly_pts, before)

    def test_pan_commits_final_release_even_without_last_move_event(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtTest import QTest
        viewport = self.view.viewport()
        start = self.view.mapFromScene(QPointF(80, 60))
        before = self.view.mapFromScene(QPointF(80, 60))
        QTest.mousePress(viewport, Qt.MiddleButton, pos=start)
        QTest.mouseRelease(viewport, Qt.MiddleButton, pos=start + QPoint(26, 18))
        after = self.view.mapFromScene(QPointF(80, 60))
        self.assertAlmostEqual(after.x() - before.x(), 26, delta=2)
        self.assertAlmostEqual(after.y() - before.y(), 18, delta=2)

    def test_focus_loss_ends_pan_and_restores_cursor(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QFocusEvent
        from PySide6.QtTest import QTest
        pos = self.view.mapFromScene(QPointF(80, 60))
        QTest.mousePress(self.view.viewport(), Qt.LeftButton, Qt.AltModifier, pos)
        self.assertTrue(self.view._is_panning)
        self.app.sendEvent(self.view, QFocusEvent(QEvent.FocusOut))
        self.assertFalse(self.view._is_panning)
        self.assertEqual(self.view.viewport().cursor().shape(), Qt.CrossCursor)

    def test_other_buttons_do_not_leak_into_scene_during_pan(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        self.scene.set_mode(CanvasMode.POLY)
        viewport = self.view.viewport()
        pos = self.view.mapFromScene(QPointF(60, 60))
        QTest.mousePress(viewport, Qt.MiddleButton, pos=pos)
        QTest.mousePress(viewport, Qt.LeftButton, pos=pos)
        QTest.mouseRelease(viewport, Qt.LeftButton, pos=pos)
        self.assertTrue(self.view._is_panning)
        QTest.mouseRelease(viewport, Qt.MiddleButton, pos=pos)
        self.assertFalse(self.view._is_panning)
        self.assertEqual(self.scene.poly_pts, [])

    def test_middle_pan_while_rectangle_dragging_does_not_leave_drawing_stuck(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        self.scene.set_mode(CanvasMode.RECT)
        viewport = self.view.viewport()
        start = self.view.mapFromScene(QPointF(35, 30))
        end = self.view.mapFromScene(QPointF(90, 70))
        completed = []
        def record(shape):
            completed.append(shape)
        self.scene.shape_drawn.connect(record)
        QTest.mousePress(viewport, Qt.LeftButton, pos=start)
        self._move(end, Qt.LeftButton, Qt.NoModifier)
        self.assertTrue(self.scene.drawing)
        QTest.mousePress(viewport, Qt.MiddleButton, pos=end)
        self.assertTrue(self.view._pan_scene_left_active)
        self._move(end + QPoint(10, 10), Qt.LeftButton | Qt.MiddleButton, Qt.NoModifier)
        QTest.mouseRelease(viewport, Qt.LeftButton, pos=end + QPoint(10, 10))
        self.assertFalse(self.scene.drawing)
        self.assertEqual(len(completed), 1)
        QTest.mouseRelease(viewport, Qt.MiddleButton, pos=end + QPoint(10, 10))
        self.assertFalse(self.view._is_panning)

    def test_pan_suspends_sam_hover_without_creating_a_shape(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        class FakeSAM:
            current_model_key = "sam3"
            def __init__(self):
                self.requests = []
            def request_inference(self, *args, **kwargs):
                self.requests.append((args, kwargs))
        self.scene.set_mode(CanvasMode.POLY)
        self.scene.sam_client = FakeSAM()
        self.scene.set_sam_enabled(True)
        self.scene._queue_sam_hover_inference(QPointF(40, 40))
        self.assertTrue(self.scene._sam_hover_timer.isActive())
        viewport = self.view.viewport()
        pos = self.view.mapFromScene(QPointF(40, 40))
        QTest.mousePress(viewport, Qt.LeftButton, Qt.AltModifier, pos)
        self.assertFalse(self.scene._sam_hover_timer.isActive())
        self.assertIsNone(self.scene._sam_hover_pending_point)
        self.scene.handle_sam_result([[30, 30], [60, 30], [60, 60]], [30, 30, 30, 30], [], 0.8, False)
        self.assertIsNone(self.scene.sam_hover_item)
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.AltModifier, pos)
        self.assertEqual(self.scene.sam_client.requests, [])


if __name__ == "__main__":
    unittest.main()
