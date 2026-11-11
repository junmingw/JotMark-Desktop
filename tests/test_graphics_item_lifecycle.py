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

    def test_handles_are_retired_after_current_event_and_can_be_recreated(self):
        from PySide6.QtWidgets import QGraphicsScene
        from shiboken6 import isValid
        from jotmark.graphics.shapes import PolyShape, PolylineShape, RectShape, RotatedRectShape

        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 500, 500)
        for shape in self._shapes():
            if not isinstance(shape, (PolyShape, PolylineShape, RectShape, RotatedRectShape)):
                continue
            with self.subTest(shape=type(shape).__name__):
                scene.addItem(shape)
                shape.setSelected(True)
                if isinstance(shape, RectShape):
                    old = shape.lt_handle
                elif isinstance(shape, RotatedRectShape):
                    old = shape.h_top
                else:
                    old = shape.handles[-1]
                shape.setSelected(False)
                self.assertTrue(isValid(old))
                self.assertIs(old.parentItem(), shape)
                self.assertFalse(old.isVisible())
                shape.setSelected(True)
                self.app.processEvents()
                self.assertTrue(isValid(old))  # this test holds a Python reference
                self.assertIsNone(old.scene())
                self.assertIsNone(old.parentItem())
                shape.setSelected(False)
                scene.removeItem(shape)
                self.app.processEvents()

    def test_vertex_removed_during_callback_remains_alive_until_next_turn(self):
        from PySide6.QtWidgets import QGraphicsScene
        from shiboken6 import isValid
        from jotmark.graphics.shapes import PolyShape, PolylineShape

        scene = QGraphicsScene()
        for shape in self._shapes():
            if not isinstance(shape, (PolyShape, PolylineShape)):
                continue
            with self.subTest(shape=type(shape).__name__):
                scene.addItem(shape)
                shape.setSelected(True)
                if isinstance(shape, PolyShape):
                    points = shape.polygon()
                    points.append(points[0])
                    shape.setPolygon(points)
                handle = shape.handles[-1]
                shape.remove_handle(handle)
                self.assertTrue(isValid(handle))
                self.assertIs(handle.parentItem(), shape)
                self.assertFalse(handle.isVisible())
                self.app.processEvents()
                self.assertIsNone(handle.parentItem())
                self.assertIsNone(handle.scene())
                scene.removeItem(shape)
                self.app.processEvents()

    def test_retired_handle_survives_parent_or_scene_disposal(self):
        from PySide6.QtWidgets import QGraphicsScene
        from shiboken6 import delete
        from jotmark.graphics.shapes import PolyShape

        scene = QGraphicsScene()
        shape = next(shape for shape in self._shapes() if isinstance(shape, PolyShape))
        scene.addItem(shape)
        shape.setSelected(True)
        shape.setSelected(False)
        delete(scene)
        self.app.processEvents()  # a deferred callback must tolerate Qt-owned destruction


if __name__ == "__main__":
    unittest.main()
