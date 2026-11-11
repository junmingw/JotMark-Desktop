"""Vertex size preferences affect presentation, never annotation data."""

import os
import tempfile
import unittest

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for vertex-size tests")
class VertexHandleSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtGui import QImage
        from jotmark.graphics.canvas import Canvas
        self.directory = tempfile.TemporaryDirectory()
        self.image = os.path.join(self.directory.name, "road.png")
        self.assertTrue(QImage(240, 120, QImage.Format_RGB32).save(self.image))
        self.scene = Canvas()
        self.scene.load_image(self.image)

    def tearDown(self):
        self.scene.clear_shapes()
        self.directory.cleanup()

    def test_limits_and_invalid_values(self):
        self.assertEqual(self.scene.vertex_handle_diameter, 7)
        for value, expected in ((1, 2), (3.6, 3.5), (100, 10.5),
                                ("bad", 7), (float("nan"), 7), (float("inf"), 7)):
            with self.subTest(value=value):
                self.scene.set_vertex_handle_diameter(value)
                self.assertEqual(self.scene.vertex_handle_diameter, expected)

    def test_existing_new_and_lazy_handles_and_ghosts_update_without_geometry_changes(self):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPolygonF
        from jotmark.data.exporter import Exporter
        from jotmark.graphics.shapes import PolyShape, PolylineShape, RectShape
        points = QPolygonF([QPointF(30, 30), QPointF(90, 30), QPointF(90, 80)])
        polygon, line = PolyShape(points, "road"), PolylineShape(points, "line")
        rect = RectShape(QRectF(150, 20, 50, 50), "car")
        for shape in (polygon, line, rect):
            self.scene.addItem(shape)
            shape.setSelected(True)
        before = Exporter.extract_shapes(self.scene)
        self.scene.set_vertex_handle_diameter(2)
        for shape in (polygon, line):
            self.assertEqual(len(shape.handles), 3)
            self.assertTrue(all(handle.boundingRect().width() == 2 for handle in shape.handles))
            self.assertEqual(shape.ghost_handle.rect().width(), 2)
        self.assertEqual(rect.lt_handle.boundingRect().width(), 7)
        self.assertEqual(Exporter.extract_shapes(self.scene), before)

        self.scene.set_vertex_handle_diameter(10.5)
        for shape in (polygon, line):
            self.assertTrue(all(handle.boundingRect().width() == 10.5 for handle in shape.handles))
            shape.setSelected(False)
            shape.setSelected(True)
            self.assertTrue(all(handle.boundingRect().width() == 10.5 for handle in shape.handles))
        new_shape = PolyShape(points, "new")
        self.scene.addItem(new_shape)
        new_shape.setSelected(True)
        self.assertTrue(all(handle.boundingRect().width() == 10.5 for handle in new_shape.handles))
        self.assertEqual(new_shape.ghost_handle.rect().width(), 10.5)

    def test_drawing_markers_follow_points_size_cancel_finish_and_image_switch(self):
        from PySide6.QtCore import QPointF
        from jotmark.graphics.canvas import CanvasMode
        for mode in (CanvasMode.POLY, CanvasMode.LINE):
            with self.subTest(mode=mode):
                self.scene.set_mode(mode)
                self.scene.set_vertex_handle_diameter(3.5)
                self.scene.poly_pts.append(QPointF(20, 20))
                self.scene.update_temp_poly()
                self.assertEqual(len(self.scene._drawing_vertex_markers), 1)
                self.assertEqual(self.scene._drawing_vertex_markers[0].rect().width(), 3.5)
                self.scene.poly_pts.extend([QPointF(60, 20), QPointF(60, 60)])
                self.scene.update_temp_poly(QPointF(80, 80))
                self.assertEqual(len(self.scene._drawing_vertex_markers), 3)
                preview = self.scene.temp_item
                self.scene.clear_shapes()
                self.assertIs(self.scene.temp_item, preview)
                self.assertIs(preview.scene(), self.scene)
                self.assertEqual(len(self.scene.poly_pts), 3)
                self.assertEqual(len(self.scene._drawing_vertex_markers), 3)
                self.scene.set_vertex_handle_diameter(9)
                self.assertTrue(all(marker.rect().width() == 9 for marker in self.scene._drawing_vertex_markers))
                self.scene.poly_pts.pop()
                self.scene.update_temp_poly()
                self.assertEqual(len(self.scene._drawing_vertex_markers), 2)
                self.scene.poly_pts.append(QPointF(60, 60))
                self.scene.update_temp_poly()
                self.scene.finish_poly_shape()
                self.assertFalse(self.scene._drawing_vertex_markers)
                self.assertFalse(self.scene.poly_pts)
                self.scene.poly_pts.append(QPointF(20, 20))
                self.scene.update_temp_poly()
                self.scene.cancel_drawing()
                self.assertFalse(self.scene._drawing_vertex_markers)
                self.scene.poly_pts.append(QPointF(20, 20))
                self.scene.update_temp_poly()
                self.scene.load_image(self.image)
                self.assertFalse(self.scene._drawing_vertex_markers)
                self.assertFalse(self.scene.poly_pts)


if __name__ == "__main__":
    unittest.main()
