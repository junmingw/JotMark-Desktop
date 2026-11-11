"""The current image position is independent of checked batch files."""

import os
import tempfile
import unittest
from unittest.mock import patch

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None


@unittest.skipUnless(PySide6, "PySide6 is required for file-position tests")
class FilePositionTests(unittest.TestCase):
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
        from main import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.paths = {}
        for index, name in enumerate(("img1.png", "img2.png", "img10.png")):
            path = os.path.join(self.temp.name, name)
            image = QImage(40 + index, 30, QImage.Format_RGB32)
            image.fill(0xff000000)
            self.assertTrue(image.save(path))
            os.utime(path, (1000 + index * 10, 1000 + index * 10))
            self.paths[name] = path
        self.win = MainWindow()
        self.win.current_dir = self.temp.name
        self.win.attribute_store.load(self.temp.name)
        self.win.file_sort_key = "name"
        self.win.file_sort_descending = False
        self.win._rebuild_file_list(list(reversed(list(self.paths.values()))))

    def tearDown(self):
        self.win._autosave_timer.stop()
        self.win._autosave_dirty = False
        self.win.close()
        self.app.processEvents()
        self.temp.cleanup()

    def _row(self, filename):
        path = self.paths[filename]
        return next(i for i in range(self.win.listFiles.count())
                    if self.win._file_item_path(self.win.listFiles.item(i)) == path)

    def _sort(self, key, descending=False):
        self.win.file_sort_key = key
        self.win.file_sort_descending = descending
        self.win._rebuild_file_list(list(self.paths.values()))

    def test_position_follows_loaded_image_and_sort_independently_of_checkboxes(self):
        from PySide6.QtCore import Qt
        self.assertEqual(self.win.current_image_path, self.paths["img1.png"])
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "1/3")
        self.assertEqual(self.win.labelSelectedCount.text(), "✓ 0/3")
        self.win.listFiles.setCurrentRow(self._row("img10.png"))
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "3/3")
        self.win.listFiles.item(self._row("img1.png")).setCheckState(Qt.Checked)
        self.assertEqual(self.win.labelSelectedCount.text(), "✓ 1/3")
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "3/3")
        self._sort("name", descending=True)
        self.assertEqual(self.win.current_image_path, self.paths["img10.png"])
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "1/3")
        self.assertEqual(self.win.labelSelectedCount.text(), "✓ 1/3")
        self._sort("modified")
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "3/3")
        self._sort("size")
        self.assertEqual(self.win.labelCurrentImagePosition.text(), f"{self._row('img10.png') + 1}/3")

    def test_failed_or_cancelled_switch_keeps_position_of_actual_canvas_image(self):
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import QPointF
        first = self.win.listFiles.currentItem()
        second = self.win.listFiles.item(1)
        self.win.scene.poly_pts.append(QPointF(5, 5))
        self.win.scene.update_temp_poly()
        with patch("main.QMessageBox.question", return_value=QMessageBox.No):
            self.win.listFiles.setCurrentItem(second)
        self.assertIs(self.win.listFiles.currentItem(), first)
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "1/3")
        self.win.scene.cancel_drawing()
        with patch.object(self.win, "_ensure_pending_annotation_saved", return_value=False):
            self.win.listFiles.setCurrentItem(second)
        self.assertIs(self.win.listFiles.currentItem(), first)
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "1/3")

    def test_delete_and_empty_list_update_position(self):
        self.win.listFiles.setCurrentRow(self._row("img10.png"))
        self.win.delete_selected_files([self.win.listFiles.item(self._row("img1.png"))])
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "2/2")
        self.win.delete_selected_files([self.win.listFiles.item(self._row("img10.png"))])
        self.assertEqual(self.win.current_image_path, self.paths["img2.png"])
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "1/1")
        self.win.delete_selected_files([self.win.listFiles.item(0)])
        self.assertEqual(self.win.labelCurrentImagePosition.text(), "—/0")
        self.assertIsNone(self.win.current_image_path)

    def test_summary_tooltips_and_position_precede_checkbox(self):
        controls = self.win.filesHeader.layout().itemAt(1).layout()
        self.assertLess(controls.indexOf(self.win.labelCurrentImagePosition), controls.indexOf(self.win.chkSelectAll))
        self.assertIn("勾选", self.win.labelSelectedCount.toolTip())
        self.assertIn("不代表", self.win.labelAnnotatedCount.toolTip())
        self.assertIn("清空", self.win.btnOverwrite.toolTip())
        self.assertIn("手工", self.win.btnOverwrite.toolTip())
        self.assertFalse(self.win.btnOverwrite.isChecked())

    def test_compact_header_fits_narrow_sidebar(self):
        self.win.resize(1100, 700)
        self.win.rightPanel.setMaximumWidth(180)
        self.win.show()
        self.win.labelCurrentImagePosition.setText("12345/99999")
        self.win.labelSelectedCount.setText("✓ 123/999")
        self.win.labelAnnotatedCount.setText("● 123/999")
        self.app.processEvents()
        label = self.win.labelCurrentImagePosition
        self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
        for summary in (self.win.labelSelectedCount, self.win.labelAnnotatedCount):
            self.assertGreaterEqual(summary.width(), summary.fontMetrics().horizontalAdvance(summary.text()))
        for widget in (self.win.chkSelectAll, self.win.btnOverwrite, self.win.btnFileExtensions,
                       self.win.btnFileSort, self.win.btnDeleteFiles):
            self.assertLessEqual(widget.geometry().right(), widget.parentWidget().width())


if __name__ == "__main__":
    unittest.main()
