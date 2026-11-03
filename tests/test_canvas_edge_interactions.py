"""Offscreen tests for cursor-anchored zoom and limited edge vertex placement."""

import os
import tempfile
import unittest

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for canvas interaction tests")
class CanvasEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtGui import QImage
        from ui.main_window import CanvasView
        from jotmark.graphics.canvas import Canvas

        self.directory = tempfile.TemporaryDirectory()
        path = os.path.join(self.directory.name, "image.png")
        self.assertTrue(QImage(240, 120, QImage.Format_RGB32).save(path))
        self.view = CanvasView()
        self.view.resize(500, 400)
        self.scene = Canvas()
        self.view.setScene(self.scene)
        self.view.show()
        self.app.processEvents()
        self.scene.load_image(path)
        self.view.reset_to_fit()
        self.app.processEvents()

    def tearDown(self):
        self.view.close()
        self.directory.cleanup()

    def test_wheel_zoom_keeps_edge_pixel_under_cursor(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        original_rect = self.scene.sceneRect()
        image_point = QPointF(2, 3)
        cursor = self.view.mapFromScene(image_point)
        self.assertTrue(self.view.viewport().rect().contains(cursor))
        before = self.view.mapToScene(cursor)

        for delta in (120, 120, 120, -120):
            wheel = QWheelEvent(
                QPointF(cursor), QPointF(self.view.viewport().mapToGlobal(cursor)),
                QPoint(), QPoint(0, delta), Qt.NoButton, Qt.ControlModifier,
                Qt.NoScrollPhase, False,
            )
            self.view.wheelEvent(wheel)
            self.app.processEvents()
            after = self.view.mapToScene(cursor)
            self.assertLess((after - before).manhattanLength(), 3)
            self.assertEqual(self.scene.sceneRect(), original_rect)
            before = after
        self.assertGreater(self.view.horizontalScrollBar().maximum(), 0)
        # Zooming back to an image that fits should restore the no-scrollbar view.
        for _ in range(4):
            before_out = self.view.mapToScene(cursor)
            self.view._zoom_at(1 / 1.15, cursor)
            if self.view.horizontalScrollBar().maximum() > 0:
                self.assertLess((self.view.mapToScene(cursor) - before_out).manhattanLength(), 3)
        self.assertEqual(self.view.horizontalScrollBar().maximum(), 0)
        self.assertEqual(self.view.verticalScrollBar().maximum(), 0)

    def test_fit_leaves_blank_space_on_all_four_sides(self):
        from PySide6.QtCore import QPointF
        viewport = self.view.viewport().rect()
        first = self.view.mapFromScene(QPointF(0, 0))
        last = self.view.mapFromScene(QPointF(240, 120))
        self.assertGreater(first.x(), 25)
        self.assertGreater(first.y(), 25)
        self.assertLess(last.x(), viewport.right() - 25)
        self.assertLess(last.y(), viewport.bottom() - 25)
        self.assertEqual(self.view.horizontalScrollBar().maximum(), 0)
        self.assertEqual(self.view.verticalScrollBar().maximum(), 0)

        # A different preference re-fits without changing the image coordinates.
        self.scene.edge_click_margin = 10
        self.view.reset_to_fit()
        self.assertEqual(self.scene.image_rect().width(), 240)
        self.assertEqual(self.scene.image_rect().height(), 120)
        self.assertIsNone(self.scene.projected_edge_vertex(QPointF(-15, 30)))

    def test_nearby_outside_clicks_project_to_image_boundary(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode

        # Shrink within the viewport so all four sides have accessible blank space.
        self.view.scale(0.55, 0.55)
        self.app.processEvents()
        self.assertEqual(self.view.horizontalScrollBar().maximum(), 0)
        self.assertEqual(self.view.verticalScrollBar().maximum(), 0)
        self.scene.edge_click_margin = 28
        self.scene.set_mode(CanvasMode.POLY)
        points = [QPointF(-12, 30), QPointF(100, -12), QPointF(252, 60), QPointF(100, 132)]
        expected = [(0, 30), (100, 0), (240, 60), (100, 120)]
        for pt, (x, y) in zip(points, expected):
            self.assertEqual(self.scene.projected_edge_vertex(pt), QPointF(x, y))
            QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(pt))
            self.assertAlmostEqual(self.scene.poly_pts[-1].x(), x, delta=1)
            self.assertAlmostEqual(self.scene.poly_pts[-1].y(), y, delta=1)
        self.assertEqual(len(self.scene.poly_pts), 4)
        self.assertIsNone(self.scene.projected_edge_vertex(QPointF(-50, 60)))
        QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(QPointF(-50, 60)))
        self.assertEqual(len(self.scene.poly_pts), 4)

        self.scene.set_mode(CanvasMode.LINE)
        QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(QPointF(-12, 30)))
        self.assertEqual(len(self.scene.poly_pts), 1)
        self.assertAlmostEqual(self.scene.poly_pts[0].x(), 0, delta=1)
        self.assertAlmostEqual(self.scene.poly_pts[0].y(), 30, delta=1)
        self.scene.edge_click_margin = 0
        QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(QPointF(100, -12)))
        self.assertEqual(len(self.scene.poly_pts), 1)

    def test_outside_doubleclick_can_finish_polyline_without_off_image_points(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        self.view.scale(0.55, 0.55)
        self.scene.set_mode(CanvasMode.LINE)
        completed = []
        self.scene.shape_drawn.connect(completed.append)
        for point in (QPointF(20, 20), QPointF(60, 40)):
            QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(point))
        QTest.mouseDClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(QPointF(250, 45)))
        self.assertEqual(len(completed), 1)
        self.assertFalse(self.scene.poly_pts)
        self.assertTrue(all(0 <= p.x() <= 240 and 0 <= p.y() <= 120 for p in completed[0].polygon()))

    def test_zoomed_and_scrolled_edge_click_still_projects_within_margin(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        from jotmark.graphics.canvas import CanvasMode
        self.scene.set_mode(CanvasMode.POLY)
        self.assertIsNotNone(self.scene.projected_edge_vertex(QPointF(-8, 30)))
        cursor = self.view.mapFromScene(QPointF(2, 30))
        self.view._zoom_at(1.5, cursor)
        self.assertGreater(self.view.horizontalScrollBar().maximum(), 0)
        self.app.processEvents()
        self.view.centerOn(QPointF(0, 60))
        self.app.processEvents()
        pt = QPointF(-8, 60)
        self.assertTrue(
            self.view.viewport().rect().contains(self.view.mapFromScene(pt)),
            (self.view.mapFromScene(pt), self.view.viewport().rect(), self.view.sceneRect(),
             self.view.horizontalScrollBar().minimum(), self.view.horizontalScrollBar().maximum(),
             self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value(),
             self.view.transform().m11(), self.view.mapToScene(self.view.viewport().rect().center())),
        )
        self.assertEqual(self.scene.projected_edge_vertex(pt), QPointF(0, 60))
        QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(pt))
        self.assertEqual(len(self.scene.poly_pts), 1)
        self.assertAlmostEqual(self.scene.poly_pts[0].x(), 0, delta=1)
        self.assertAlmostEqual(self.scene.poly_pts[0].y(), 60, delta=1)
        self.assertIsNone(self.scene.projected_edge_vertex(QPointF(-60, 60)))

        # The same behavior must work for polylines; other modes and disabled
        # margins must not silently create geometry outside the image.
        self.scene.set_mode(CanvasMode.LINE)
        QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(pt))
        self.assertEqual(len(self.scene.poly_pts), 1)
        self.scene.edge_click_margin = 0
        self.assertIsNone(self.scene.projected_edge_vertex(pt))
        self.scene.edge_click_margin = 40
        for center, outside, boundary in (
            (QPointF(240, 60), QPointF(248, 60), QPointF(240, 60)),
            (QPointF(100, 0), QPointF(100, -8), QPointF(100, 0)),
            (QPointF(100, 120), QPointF(100, 128), QPointF(100, 120)),
        ):
            self.view.centerOn(center)
            self.app.processEvents()
            self.assertEqual(self.scene.projected_edge_vertex(outside), boundary)
            QTest.mouseClick(self.view.viewport(), Qt.LeftButton, pos=self.view.mapFromScene(outside))
            self.assertAlmostEqual(self.scene.poly_pts[-1].x(), boundary.x(), delta=1)
            self.assertAlmostEqual(self.scene.poly_pts[-1].y(), boundary.y(), delta=1)
        self.view.centerOn(QPointF(240, 60))
        self.app.processEvents()
        self.assertIsNone(self.scene.projected_edge_vertex(pt))  # left edge is offscreen
        self.scene.set_mode(CanvasMode.RECT)
        self.assertIsNone(self.scene.projected_edge_vertex(pt))
        self.scene.set_mode(CanvasMode.LINE)
        self.scene.set_sam_enabled(True)
        self.assertIsNone(self.scene.projected_edge_vertex(pt))


if __name__ == "__main__":
    unittest.main()
