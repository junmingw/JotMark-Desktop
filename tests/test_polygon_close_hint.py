"""Polygon-start visual hint follows real closure behavior without changing geometry."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for canvas tests")
class PolygonCloseHintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtGui import QImage
        from jotmark.graphics.canvas import Canvas, CanvasMode
        from ui.main_window import CanvasView
        self.tmp = tempfile.TemporaryDirectory()
        self.image = os.path.join(self.tmp.name, "road.png")
        self.assertTrue(QImage(240, 120, QImage.Format_RGB32).save(self.image))
        self.scene = Canvas()
        self.view = CanvasView()
        self.view.resize(500, 400)
        self.view.setScene(self.scene)
        self.view.show()
        self.scene.load_image(self.image)
        self.view.reset_to_fit()
        self.scene.set_mode(CanvasMode.POLY)
        self.app.processEvents()
        self.completed = []
        self.scene.shape_drawn.connect(self.completed.append)

    def tearDown(self):
        self.scene.cancel_drawing()
        self.view.close()
        self.app.processEvents()
        self.tmp.cleanup()

    def _click(self, point):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(point))
        self.app.processEvents()

    def _move(self, point):
        from PySide6.QtCore import QPointF
        from PySide6.QtTest import QTest
        # The OS cursor can remain at the same global location across tests;
        # force an actual move before moving to the requested location.
        QTest.mouseMove(self.view.viewport(), self.view.mapFromScene(QPointF(200, 105)), delay=5)
        QTest.mouseMove(self.view.viewport(), self.view.mapFromScene(point), delay=5)
        self.app.processEvents()

    def _triangle(self):
        from PySide6.QtCore import QPointF
        for point in (QPointF(40, 40), QPointF(150, 40), QPointF(150, 90)):
            self._click(point)
        self.assertEqual(len(self.scene.poly_pts), 3)

    def test_highlight_matches_close_range_and_click_preserves_three_vertices(self):
        from PySide6.QtCore import QPointF
        self._triangle()
        original = [(p.x(), p.y()) for p in self.scene.poly_pts]
        self._move(QPointF(75, 40))
        first = self.scene._drawing_vertex_markers[0]
        self.assertAlmostEqual(first.rect().width(), 7.0)
        self._move(QPointF(43, 40))
        self.assertAlmostEqual(first.rect().width(), 9.8)
        self.assertNotEqual(first.brush().color(), self.scene._drawing_vertex_markers[1].brush().color())
        self.assertEqual([(p.x(), p.y()) for p in self.scene.poly_pts], original)
        self._click(QPointF(43, 40))
        self.assertEqual(len(self.completed), 1)
        self.assertEqual([(p.x(), p.y()) for p in self.completed[0].polygon()], original)
        self.assertFalse(self.scene._drawing_vertex_markers)

    def test_disable_and_scale_change_only_appearance(self):
        from PySide6.QtCore import QPointF
        self._triangle()
        self._move(QPointF(42, 40))
        marker = self.scene._drawing_vertex_markers[0]
        self.scene.set_polygon_close_hint(True, 1.5)
        self.assertAlmostEqual(marker.rect().width(), 10.5)
        self.scene.set_vertex_handle_diameter(3.5)
        self.assertAlmostEqual(marker.rect().width(), 5.25)
        self.scene.set_polygon_close_hint(False, 1.5)
        self.assertAlmostEqual(marker.rect().width(), 3.5)
        self._click(QPointF(42, 40))
        self.assertEqual(len(self.completed), 1)
        self.assertEqual(len(self.completed[0].polygon()), 3)

    def test_ineligible_modes_and_sam_do_not_show_hint(self):
        from PySide6.QtCore import QPointF
        from jotmark.graphics.canvas import CanvasMode
        self.scene.poly_pts = [QPointF(40, 40), QPointF(150, 40)]
        self.scene.update_temp_poly(QPointF(41, 40))
        self.assertEqual(self.scene._drawing_vertex_markers[0].rect().width(), 7)
        self.scene.poly_pts.append(QPointF(150, 90))
        self.scene.update_temp_poly(QPointF(41, 40))
        self.assertAlmostEqual(self.scene._drawing_vertex_markers[0].rect().width(), 9.8)
        self.scene.sam_client = SimpleNamespace(current_model_key="sam3")
        self.scene.set_sam_enabled(True)
        self.assertEqual(self.scene._drawing_vertex_markers[0].rect().width(), 7)
        self.scene.set_sam_enabled(False)
        self.assertAlmostEqual(self.scene._drawing_vertex_markers[0].rect().width(), 9.8)
        self.scene.set_mode(CanvasMode.LINE)
        self.scene.poly_pts = [QPointF(40, 40), QPointF(150, 40), QPointF(150, 90)]
        self.scene.update_temp_poly(QPointF(41, 40))
        self.assertEqual(self.scene._drawing_vertex_markers[0].rect().width(), 7)

    def test_projected_edge_highlights_and_closes(self):
        from PySide6.QtCore import QPointF
        for point in (QPointF(-6, 55), QPointF(100, 30), QPointF(100, 90)):
            self._click(point)
        original = [(p.x(), p.y()) for p in self.scene.poly_pts]
        self.assertAlmostEqual(original[0][0], 0)
        self._move(QPointF(-8, 55))
        self.assertAlmostEqual(self.scene._drawing_vertex_markers[0].rect().width(), 9.8)
        self._click(QPointF(-8, 55))
        self.assertEqual([(p.x(), p.y()) for p in self.completed[0].polygon()], original)

    def test_zoom_pan_and_leaving_clear_or_refresh_stale_hint(self):
        from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        self._triangle()
        self._move(QPointF(42, 40))
        marker = self.scene._drawing_vertex_markers[0]
        self.assertAlmostEqual(marker.rect().width(), 9.8)
        position = self.view.mapFromScene(QPointF(42, 40))
        wheel = QWheelEvent(QPointF(position), QPointF(self.view.viewport().mapToGlobal(position)),
                            QPoint(), QPoint(0, 120), Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
        self.view.wheelEvent(wheel)
        self.assertAlmostEqual(marker.rect().width(), 9.8)
        self.scene.suspend_hover_for_pan()
        self.assertAlmostEqual(marker.rect().width(), 7)
        self.scene.refresh_pointer_after_pan(QPointF(42, 40))
        self.assertAlmostEqual(marker.rect().width(), 9.8)
        self.view.leaveEvent(QEvent(QEvent.Leave))
        self.assertAlmostEqual(marker.rect().width(), 7)
        self.assertEqual(len(self.scene.poly_pts), 3)

    def test_scale_setting_clamps_and_invalid_values_are_safe(self):
        for value, expected in ((0, 1.0), (1.36, 1.4), (2.0, 1.5), ("bad", 1.4), (float("nan"), 1.4)):
            with self.subTest(value=value):
                self.scene.set_polygon_close_hint(True, value)
                self.assertEqual(self.scene.polygon_close_hint_scale, expected)

    def test_threshold_is_unchanged_and_resize_clears_stale_highlight(self):
        from PySide6.QtCore import QPointF
        self._triangle()
        start = self.scene.poly_pts[0]
        self.assertTrue(self.scene._near_polygon_start(start + QPointF(9.99, 0)))
        self.assertFalse(self.scene._near_polygon_start(start + QPointF(10.0, 0)))
        self._move(QPointF(42, 40))
        marker = self.scene._drawing_vertex_markers[0]
        self.assertAlmostEqual(marker.rect().width(), 9.8)
        with patch.object(self.scene, "clear_drawing_pointer", wraps=self.scene.clear_drawing_pointer) as clear:
            self.view.resize(520, 420)
            self.app.processEvents()
        clear.assert_called()
        # A resize can also cause Qt to send a fresh mouse-move event; in
        # that case a newly correct hint is allowed to appear again.
        if self.scene._drawing_pointer is None:
            self.assertAlmostEqual(marker.rect().width(), 7)
        else:
            self.assertEqual(marker.rect().width() > 7, self.scene._near_polygon_start(self.scene._drawing_pointer))
        self.assertEqual(len(self.scene.poly_pts), 3)


if __name__ == "__main__":
    unittest.main()
