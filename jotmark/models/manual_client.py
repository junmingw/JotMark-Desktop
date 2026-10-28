"""No-op inference client used by the lightweight manual-annotation edition.

Keeping this small Qt-compatible client lets the canvas and main window retain
their normal signal wiring without importing PyTorch, SAM or YOLO.
"""

from PySide6.QtCore import QObject, Signal


# The model selector deliberately has nothing to show in manual-only mode.
SAM_MODEL_MAP = {}


class ManualAnnotationClient(QObject):
    """API-compatible, intentionally inert replacement for ``SAMClient``."""

    model_status_changed = Signal(bool, str)
    inference_result = Signal(list, list, list, float, bool)
    text_result_ready = Signal(list, str)
    merge_result_ready = Signal(list, list, list, float)
    image_ready = Signal(int, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = None
        self.processor = None
        self.current_model_key = None
        self.current_model_type = None
        self.device = "cpu"
        self._sam3_worker = None

    def load_model_by_key(self, _model_key):
        self.model_status_changed.emit(False, "手工标注版未安装辅助模型。")

    def set_image(self, _image_path):
        return None

    def request_inference(self, _x, _y, _is_click):
        return None

    def request_text_inference(self, _prompt_text):
        return None

    def request_merge_inference(self, _points):
        return None

    def supports_text_prompt(self):
        return False

    def cleanup(self):
        return None
