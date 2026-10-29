#!/usr/bin/env python3
"""Open JotMark's safe category/attribute conversion interface directly.

Usage:
    python3 scripts/open_category_conversion.py /path/to/annotation-project
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from ui.category_conversion_dialog import CategoryConversionDialog


def main():
    parser = argparse.ArgumentParser(description="打开 JotMark 类别 / 属性安全转换工具")
    parser.add_argument(
        "source_directory",
        nargs="?",
        default="",
        help="可选：源标注项目目录；不填则在界面中选择。",
    )
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = CategoryConversionDialog(args.source_directory)
    dialog.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
