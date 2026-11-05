"""Regression tests for Qt callbacks during shape construction and teardown."""

import gc
import os
import unittest

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for graphics lifecycle tests")
class GraphicsItemLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _shapes():
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPolygonF
        from jotmark.graphics.shapes import (
            PointShape, PolyShape, PolylineShape, PoseShape, RectShape, RotatedRectShape,
        )
        points = QPolygonF([QPointF(10, 10), QPointF(30, 10), QPointF(20, 30)])
        return [
            RectShape(QRectF(10, 10, 20, 20), "box"),
            PolyShape(points, "polygon"),
            PolylineShape(points, "line"),
            PointShape(QPointF(20, 20), "point"),
            RotatedRectShape(40, 40, 20, 20, 20, "rotated"),
            PoseShape(QRectF(10, 10, 20, 20), {"keypoints": [], "connections": []}, "pose"),
        ]

    def test_callbacks_are_forwarded_when_shape_is_not_ready(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtWidgets import QGraphicsItem
        for shape in self._shapes():
            with self.subTest(shape=type(shape).__name__):
                shape._graphics_ready = False
                point = QPointF(15, 20)
                self.assertEqual(shape.itemChange(QGraphicsItem.ItemPositionChange, point), point)
                shape.itemChange(QGraphicsItem.ItemPositionHasChanged, point)
                shape.itemChange(QGraphicsItem.ItemSelectedHasChanged, True)
                shape._graphics_ready = True

    def test_missing_temporary_flag_cannot_raise_from_item_change(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 500, 500)
        for shape in self._shapes():
            if not hasattr(shape, "is_temp"):
                continue
            with self.subTest(shape=type(shape).__name__):
                scene.addItem(shape)
                old_value = shape.is_temp
                del shape.is_temp
                try:
                    shape.itemChange(QGraphicsItem.ItemVisibleChange, True)
                    shape.itemChange(QGraphicsItem.ItemSelectedHasChanged, True)
                    shape.itemChange(QGraphicsItem.ItemPositionHasChanged, QPointF(5, 5))
                    shape.setVisible(False)
                    shape.setVisible(True)
                    shape.setPos(100, 100)
                    shape.setSelected(True)
                finally:
                    shape.is_temp = old_value
                    shape.setSelected(False)
                    scene.removeItem(shape)

    def test_repeated_selection_movement_and_removal_for_mixed_shapes(self):
        from PySide6.QtWidgets import QGraphicsScene
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 500, 500)
        for _ in range(30):
            shapes = self._shapes()
            for shape in shapes:
                scene.addItem(shape)
                shape.setSelected(True)
                shape.setPos(40, 40)
                shape.setSelected(False)
            for shape in shapes:
                scene.removeItem(shape)
            shapes.clear()
            self.app.processEvents()
            gc.collect()
        self.assertEqual(len(scene.items()), 0)


if __name__ == "__main__":
    unittest.main()
