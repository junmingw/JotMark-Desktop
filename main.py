import sys
import os
import shutil
import tempfile
import re
import math
from copy import deepcopy
from datetime import datetime
from contextlib import nullcontext


# Evaluate this before importing optional AI/training modules.  The manual
# deployment uses only PySide6, OpenCV, NumPy and Pillow.
MANUAL_ONLY = os.environ.get("JOTMARK_MANUAL_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
# Full desktop builds start in manual mode by default.  Set this explicitly
# only when a workstation is dedicated to AI-assisted annotation and the
# operator accepts loading the configured default model at startup.
AUTOLOAD_ASSISTANT = os.environ.get("JOTMARK_AUTOLOAD_ASSISTANT", "").strip().lower() in {"1", "true", "yes", "on"}

# ============================================================
# PyInstaller 打包运行环境资源路径适配
# ============================================================
if getattr(sys, 'frozen', False):
    import multiprocessing
    multiprocessing.freeze_support()

if MANUAL_ONLY:
    # The lightweight distribution does not contain the training worker code.
    def dispatch_worker():
        return None
else:
    from TrainingPlatform.core.worker_runtime import dispatch_worker

if __name__ == "__main__":
    worker_exit_code = dispatch_worker()
    if worker_exit_code is not None:
        sys.exit(worker_exit_code)

if getattr(sys, 'frozen', False):
    # PyInstaller 6+ 默认将所有数据文件 (--add-data) 存放在 _internal 目录 (即 sys._MEIPASS)
    # 而可执行文件启动时的默认工作目录是 dist/JotMark，导致相对路径 "ui/icon/..." 无法读取。
    # 切换工作目录至 _internal，使所有的 "ui/icon/..."、"jotmark/config/..." 相对路径完美命中。
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    os.chdir(base_dir)
    try:
        from PySide6.QtCore import QDir
        QDir.setCurrent(base_dir)
    except Exception:
        pass

import json
from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog, QInputDialog, QMessageBox, QLabel, \
    QListWidgetItem, QDialog, QMenu, QAbstractItemView, QProgressBar, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, \
    QCheckBox, QListWidget, QFrame, QWidget, QPlainTextEdit
from PySide6.QtCore import Qt, QPointF, QRectF, QThread, Signal, QSize, QEvent, QSettings, QTimer
from PySide6.QtGui import QPainter, QIcon, QPixmap, QColor, QBrush, QAction, QActionGroup, QPolygonF, QMovie, QKeySequence, QShortcut, QPainterPath
import cv2
import numpy as np
from PIL import Image
from ui.main_window import Ui_MainWindow, TemplateSelectorWidget, FormatSelectorWidget
from ui.template_dialog import SkeletonTemplateDialog
from ui.model_selector_dialog import ModelSelectorDialog
from ui.theme import DARK_THEME, LIGHT_THEME
from jotmark.graphics.canvas import Canvas, CanvasMode, get_shape_scene_path
from jotmark.graphics.geometry import mask_to_annotation_geometry, fit_obb_to_bounds, obb_corners
from jotmark.data.exporter import Exporter
from jotmark.data.annotation_safety import archive_annotation, backup_previous_annotation, ensure_unique_image_stem, file_fingerprint
from jotmark.data.attribute_schema import ATTRIBUTE_SCHEMA_FILE, ClassAttributeStore
from jotmark.graphics.shapes import RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape
from jotmark.config.pose_template import TemplateManager
from utils.message import DialogOver
from jotmark.config.paths import get_weights_base_dir
from ui.attribute_widgets import AttributeSchemaDialog
from ui.category_conversion_dialog import CategoryConversionDialog
from ui.settings_dialog import SettingsDialog

if MANUAL_ONLY:
    from jotmark.models.manual_client import ManualAnnotationClient as SAMClient, SAM_MODEL_MAP

    TrainingPlatformWindow = None
    YoloPredictorWorker = None
    torch = None

    def resolve_device():
        return "cpu"

    def inference_context(_device):
        return nullcontext()
else:
    from TrainingPlatform.ui.main_window import MainWindow as TrainingPlatformWindow
    import torch
    from jotmark.models.sam_client import SAMClient, SAM_MODEL_MAP
    from jotmark.models.yolo_predictor import YoloPredictorWorker
    from jotmark.config.runtime import inference_context, resolve_device


# Keep the desktop file picker aligned with the online service and Pillow's
# common image support.  TIFF/WebP are especially common in industrial data.
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff', '.gif')
ANNOTATION_EXTENSION_BY_FORMAT = {
    "json": ".json",
    "yolo": ".txt",
    "xml": ".xml",
    "curvelanes": ".lines.txt",
}
DEFAULT_CLASS_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples", "classes.example.txt")


def annotation_base_path(image_path, annotation_output_dir=""):
    """Build a sidecar base path while keeping an image's original filename."""
    if annotation_output_dir:
        return os.path.join(annotation_output_dir, os.path.splitext(os.path.basename(image_path))[0])
    return os.path.splitext(image_path)[0]


def _protect_batch_write(path, expected_fingerprint):
    """Back up a batch target and reject changes during inference."""
    if file_fingerprint(path) != expected_fingerprint:
        raise RuntimeError(f"批量预测期间标注文件被外部修改：{path}")
    backup_previous_annotation(path)
    if file_fingerprint(path) != expected_fingerprint:
        raise RuntimeError(f"备份期间标注文件被外部修改：{path}")


def _reject_json_constant(value):
    raise ValueError(f"JSON 包含无效数值：{value}")


def _verify_json_image_identity(document, image_path):
    source = document.get("imagePath")
    if source is None or source == "" or not image_path:
        return
    if not isinstance(source, str):
        raise ValueError("JSON 的 imagePath 必须是字符串")
    source_name = source.replace("\\", "/").rsplit("/", 1)[-1]
    if os.path.normcase(source_name) != os.path.normcase(os.path.basename(image_path)):
        raise ValueError(f"JSON 属于图片“{source_name}”，与当前图片不一致")


class SamBatchWorker(QThread):
    progress = Signal(int, int, str)  # current, total, filename
    finished = Signal(int, int)  # processed, total
    error = Signal(str)

    def __init__(self, processor, model, img_paths, prompts, current_format, class_list, canvas_mode, overwrite=False, device=None, annotation_output_dir=""):
        super().__init__()
        self.processor = processor
        self.model = model
        self.img_paths = img_paths
        self.prompts = prompts
        self.current_format = current_format
        self.class_list = list(class_list)
        self.canvas_mode = canvas_mode
        self.is_cancelled = False
        self.overwrite = overwrite
        self.device = device or resolve_device()
        self.annotation_output_dir = annotation_output_dir or ""

    def run(self):
        total = len(self.img_paths)
        processed = 0

        try:
            if self.annotation_output_dir:
                os.makedirs(self.annotation_output_dir, exist_ok=True)
            for idx, img_path in enumerate(self.img_paths):
                if self.is_cancelled:
                    break

                filename = os.path.basename(img_path)
                self.progress.emit(idx, total, filename)
                ensure_unique_image_stem(img_path, SUPPORTED_IMAGE_EXTENSIONS)
                base_name = annotation_base_path(img_path, self.annotation_output_dir)
                extension = ANNOTATION_EXTENSION_BY_FORMAT.get(self.current_format)
                if not extension:
                    raise ValueError(f"批量标注不支持格式：{self.current_format}")
                target_path = base_name + extension
                expected_fingerprint = file_fingerprint(target_path)
                if not self.overwrite and expected_fingerprint is not None and self.current_format != "json":
                    raise RuntimeError(f"为保护已有标注，批量追加暂不支持此格式：{target_path}。请先备份并使用 JSON，或明确选择覆盖。")
                document_metadata = {}

                # 1. 加载图像并获取宽高
                pil_img = Image.open(img_path).convert("RGB")
                w_img, h_img = pil_img.size

                # 2. 提取特征
                with torch.inference_mode(), inference_context(self.device):
                    state = self.processor.set_image(pil_img)

                # 3. 针对每个提示词逐一推理并整合结果
                shapes_data = []
                for prompt in self.prompts:
                    if self.is_cancelled:
                        break

                    with torch.inference_mode(), inference_context(self.device):
                        out_state = self.processor.set_text_prompt(prompt=prompt, state=state)

                        masks = out_state.get("masks", [])
                        scores = out_state.get("scores", [])
                        boxes = out_state.get("boxes", [])

                        if len(masks) > 0:
                            for i in range(len(masks)):
                                mask_np = masks[i].cpu().numpy() if torch.is_tensor(masks[i]) else masks[i]
                                mask_np = np.squeeze(mask_np)

                                score_val = float(scores[i].cpu() if torch.is_tensor(scores[i]) else scores[i])
                                box = boxes[i].cpu().numpy() if torch.is_tensor(boxes[i]) else boxes[i]

                                if box.ndim > 1:
                                    box = box.squeeze()
                                poly_pts, rect_xywh, rect_obb = mask_to_annotation_geometry(mask_np)

                                if poly_pts:
                                    shapes_data.append({
                                        "label": prompt,
                                        "type": "polygon",
                                        "points": poly_pts,
                                        "rect": rect_xywh,
                                        "obb": rect_obb,
                                        "score": score_val
                                    })

                if self.is_cancelled:
                    break

                # 4. 后台直接保存至文件
                if shapes_data:
                    export_shapes = []
                    
                    for s in shapes_data:
                        label = s["label"]
                        poly_pts = s["points"]
                        rect_xywh = s["rect"]
                        rect_obb = s["obb"]
                        
                        if self.canvas_mode == 1:  # CanvasMode.RECT
                            x, y, w, h = rect_xywh
                            export_shapes.append({
                                "label": label,
                                "type": "rectangle",
                                "points": [[x, y], [x + w, y + h]]
                            })
                        elif self.canvas_mode == 2:  # CanvasMode.POLY
                            export_shapes.append({
                                "label": label,
                                "type": "polygon",
                                "points": poly_pts
                            })
                        elif self.canvas_mode == 4:  # CanvasMode.RBOX
                            if rect_obb and len(rect_obb) == 5:
                                cx, cy, w, h, angle = rect_obb
                                cx, cy, w, h, angle = fit_obb_to_bounds(cx, cy, w, h, angle, (0, 0, w_img, h_img))
                                points = [[x, y] for x, y in obb_corners(cx, cy, w, h, angle)]
                                export_shapes.append({
                                    "label": label,
                                    "type": "obb",
                                    "points": points,
                                    "rect": [cx, cy, w, h],
                                    "angle": angle
                                })

                    # Load existing shapes if we are in merge mode (not self.overwrite)
                    all_shapes = []
                    if not self.overwrite:
                        if self.current_format == "json":
                            json_path = base_name + ".json"
                            if os.path.exists(json_path):
                                try:
                                    import json
                                    with open(json_path, "r", encoding="utf-8") as f:
                                        data_json = json.load(f, parse_constant=_reject_json_constant)
                                    if not isinstance(data_json, dict) or not isinstance(data_json.get("shapes"), list):
                                        raise ValueError("JSON 标注必须包含 shapes 数组")
                                    _verify_json_image_identity(data_json, img_path)
                                    document_metadata = {key: deepcopy(value) for key, value in data_json.items()
                                                         if key not in {"shapes", "imagePath", "imageHeight", "imageWidth"}}
                                    for s in data_json.get("shapes", []):
                                        if not isinstance(s, dict):
                                            raise ValueError("JSON 标注包含无效的图形")
                                        all_shapes.append({
                                            "label": s.get("label", ""),
                                            "type": s.get("shape_type", "rectangle"),
                                            "points": s.get("points", []),
                                            "rect": s.get("rect", [0, 0, 0, 0]),
                                            "angle": s.get("angle", 0.0),
                                            "keypoints": s.get("keypoints", []),
                                            "kpt_shape": s.get("kpt_shape", []),
                                            "template_name": s.get("template_name", ""),
                                            "attributes": s.get("attributes", {}),
                                            "_json_metadata": deepcopy(s),
                                        })
                                except Exception as e:
                                    raise RuntimeError(f"读取已有 JSON 标注失败，未覆盖 {json_path}: {e}") from e
                        elif self.current_format == "yolo":
                            txt_path = base_name + ".txt"
                            if os.path.exists(txt_path):
                                try:
                                    with open(txt_path, "r", encoding="utf-8") as f:
                                        for line in f:
                                            parts = line.strip().split()
                                            if len(parts) >= 5:
                                                cls_id = int(parts[0])
                                                if cls_id < len(self.class_list):
                                                    label = self.class_list[cls_id]
                                                else:
                                                    label = f"class_{cls_id}"
                                                cx, cy, w, h = map(float, parts[1:5])
                                                abs_cx = cx * w_img
                                                abs_cy = cy * h_img
                                                abs_w = w * w_img
                                                abs_h = h * h_img
                                                x1 = abs_cx - abs_w / 2
                                                y1 = abs_cy - abs_h / 2
                                                all_shapes.append({
                                                    "label": label,
                                                    "type": "rectangle",
                                                    "points": [[x1, y1], [x1 + abs_w, y1 + abs_h]]
                                                })
                                except Exception as e:
                                    print(f"加载已有 YOLO 标注出错 {txt_path}: {e}")
                        elif self.current_format == "xml":
                            xml_path = base_name + ".xml"
                            if os.path.exists(xml_path):
                                try:
                                    import xml.etree.ElementTree as ET
                                    tree = ET.parse(xml_path)
                                    root = tree.getroot()
                                    for obj in root.findall("object"):
                                        label = obj.find("name").text
                                        bndbox = obj.find("bndbox")
                                        x1 = float(bndbox.find("xmin").text)
                                        y1 = float(bndbox.find("ymin").text)
                                        x2 = float(bndbox.find("xmax").text)
                                        y2 = float(bndbox.find("ymax").text)
                                        all_shapes.append({
                                            "label": label,
                                            "type": "rectangle",
                                            "points": [[x1, y1], [x2, y2]]
                                        })
                                except Exception as e:
                                    print(f"加载已有 XML 标注出错 {xml_path}: {e}")

                    # Add new prediction shapes to all_shapes
                    all_shapes.extend(export_shapes)

                    if all_shapes:
                        _protect_batch_write(target_path, expected_fingerprint)
                        if self.current_format == "json":
                            out_path = base_name + ".json"
                            Exporter.save_json(out_path, img_path, w_img, h_img, all_shapes, document_metadata)
                        elif self.current_format == "yolo":
                            out_path = base_name + ".txt"
                            Exporter.save_yolo(out_path, w_img, h_img, all_shapes, self.class_list)
                        elif self.current_format == "xml":
                            out_path = base_name + ".xml"
                            Exporter.save_xml(out_path, img_path, w_img, h_img, all_shapes)
                    elif self.overwrite:
                        # Only clear the active export format.  JSON, YOLO and
                        # XML may intentionally coexist for the same image.
                        ext = ANNOTATION_EXTENSION_BY_FORMAT.get(self.current_format)
                        out_path = base_name + ext if ext else None
                        if out_path and os.path.exists(out_path):
                            _protect_batch_write(out_path, expected_fingerprint)
                            archive_annotation(out_path)

                processed += 1

            self.finished.emit(processed, total)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))
class YoloBatchPredictorWorker(QThread):
    progress = Signal(int, int, str)  # current, total, filename
    finished = Signal(int, int, list)  # processed, total, detected_classes
    error = Signal(str)

    def __init__(self, predictor, img_paths, current_format, class_list, template_manager=None, classes=None, overwrite=False, annotation_output_dir=""):
        super().__init__()
        self.predictor = predictor
        self.img_paths = img_paths
        self.current_format = current_format
        self.class_list = list(class_list)
        self.template_manager = template_manager
        self.is_cancelled = False
        self.classes = classes
        self.overwrite = overwrite
        self.annotation_output_dir = annotation_output_dir or ""

    def run(self):
        import cv2
        import numpy as np
        from PIL import Image
        from jotmark.data.exporter import Exporter
        from PySide6.QtCore import QRectF, QPointF
        from PySide6.QtGui import QPolygonF

        total = len(self.img_paths)
        processed = 0
        detected_classes = set()

        try:
            if self.annotation_output_dir:
                os.makedirs(self.annotation_output_dir, exist_ok=True)
            for idx, img_path in enumerate(self.img_paths):
                if self.is_cancelled:
                    break

                filename = os.path.basename(img_path)
                self.progress.emit(idx, total, filename)

                ensure_unique_image_stem(img_path, SUPPORTED_IMAGE_EXTENSIONS)
                base_name = annotation_base_path(img_path, self.annotation_output_dir)
                extension = ANNOTATION_EXTENSION_BY_FORMAT.get(self.current_format)
                if not extension:
                    raise ValueError(f"批量预测不支持格式：{self.current_format}")
                target_path = base_name + extension
                expected_fingerprint = file_fingerprint(target_path)
                if not self.overwrite and expected_fingerprint is not None and self.current_format != "json":
                    raise RuntimeError(f"为保护已有标注，批量追加暂不支持此格式：{target_path}。请先备份并使用 JSON，或明确选择覆盖。")
                document_metadata = {}

                # 1. 获取图像尺寸
                try:
                    with Image.open(img_path) as pil_img:
                        w_img, h_img = pil_img.size
                except Exception as e:
                    print(f"无法读取图片尺寸 {img_path}: {e}")
                    processed += 1
                    continue

                existing_shapes = []

                # 1.1 读取并保留已有的标注（以实现增量合并预测）
                if not self.overwrite:
                    if self.current_format == "json":
                        json_path = base_name + ".json"
                        if os.path.exists(json_path):
                            try:
                                import json
                                with open(json_path, "r", encoding="utf-8") as f:
                                    data_json = json.load(f, parse_constant=_reject_json_constant)
                                if not isinstance(data_json, dict) or not isinstance(data_json.get("shapes"), list):
                                    raise ValueError("JSON 标注必须包含 shapes 数组")
                                _verify_json_image_identity(data_json, img_path)
                                document_metadata = {key: deepcopy(value) for key, value in data_json.items()
                                                     if key not in {"shapes", "imagePath", "imageHeight", "imageWidth"}}
                                for s in data_json.get("shapes", []):
                                    if not isinstance(s, dict):
                                        raise ValueError("JSON 标注包含无效的图形")
                                    existing_shapes.append({
                                        "label": s.get("label", ""),
                                        "type": s.get("shape_type", "rectangle"),
                                        "points": s.get("points", []),
                                        "rect": s.get("rect", [0, 0, 0, 0]),
                                        "angle": s.get("angle", 0.0),
                                        "keypoints": s.get("keypoints", []),
                                        "kpt_shape": s.get("kpt_shape", []),
                                        "template_name": s.get("template_name", ""),
                                        "attributes": s.get("attributes", {}),
                                        "_json_metadata": deepcopy(s),
                                    })
                            except Exception as e:
                                raise RuntimeError(f"读取已有 JSON 标注失败，未覆盖 {json_path}: {e}") from e
                    elif self.current_format == "yolo":
                        txt_path = base_name + ".txt"
                        if os.path.exists(txt_path):
                            try:
                                with open(txt_path, "r", encoding="utf-8") as f:
                                    for line in f:
                                        parts = line.strip().split()
                                        if len(parts) >= 5:
                                            cls_id = int(parts[0])
                                            if cls_id < len(self.class_list):
                                                label = self.class_list[cls_id]
                                            else:
                                                label = f"class_{cls_id}"
                                            cx, cy, w, h = map(float, parts[1:5])
                                            abs_cx = cx * w_img
                                            abs_cy = cy * h_img
                                            abs_w = w * w_img
                                            abs_h = h * h_img
                                            x1 = abs_cx - abs_w / 2
                                            y1 = abs_cy - abs_h / 2
                                            existing_shapes.append({
                                                "label": label,
                                                "type": "rectangle",
                                                "points": [[x1, y1], [x1 + abs_w, y1 + abs_h]]
                                            })
                            except Exception as e:
                                print(f"加载已有 YOLO 标注出错 {txt_path}: {e}")
                    elif self.current_format == "xml":
                        xml_path = base_name + ".xml"
                        if os.path.exists(xml_path):
                            try:
                                import xml.etree.ElementTree as ET
                                tree = ET.parse(xml_path)
                                root = tree.getroot()
                                for obj in root.findall("object"):
                                    label = obj.find("name").text
                                    bndbox = obj.find("bndbox")
                                    x1 = float(bndbox.find("xmin").text)
                                    y1 = float(bndbox.find("ymin").text)
                                    x2 = float(bndbox.find("xmax").text)
                                    y2 = float(bndbox.find("ymax").text)
                                    existing_shapes.append({
                                        "label": label,
                                        "type": "rectangle",
                                        "points": [[x1, y1], [x2, y2]]
                                    })
                            except Exception as e:
                                print(f"加载已有 XML 标注出错 {xml_path}: {e}")

                # 2. 运行 YOLO 同步预测
                shapes = self.predictor.predict_sync(img_path, classes=self.classes)
                
                # 3. 解析预测结果并与已有标注进行 IoU 重合度去重过滤
                def get_ext_rect(s):
                    stype = s.get("type", "rectangle")
                    if stype in ["rectangle", "polygon", "obb"] and s.get("points"):
                        pts = s["points"]
                        xs = [p[0] for p in pts]
                        ys = [p[1] for p in pts]
                        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
                    elif stype == "pose" and s.get("rect"):
                        cx, cy, w, h = s["rect"]
                        return QRectF(cx - w/2, cy - h/2, w, h)
                    return None

                def get_iou(r1, r2):
                    if not r1 or not r2:
                        return 0.0
                    intersection = r1.intersected(r2)
                    inter_area = max(0.0, intersection.width()) * max(0.0, intersection.height())
                    area1 = max(0.0, r1.width()) * max(0.0, r1.height())
                    area2 = max(0.0, r2.width()) * max(0.0, r2.height())
                    union_area = area1 + area2 - inter_area
                    if union_area > 0.0:
                        return inter_area / union_area
                    return 0.0

                export_shapes = list(existing_shapes)
                
                for s in shapes:
                    shape_type = s["type"]
                    label = s["label"]
                    data = s["data"]
                    detected_classes.add(label)

                    # 计算预测框的外接矩形
                    new_rect = None
                    if shape_type == "rect":
                        new_rect = data
                    elif shape_type in ["poly", "rbox"]:
                        new_rect = data.boundingRect()
                    elif shape_type == "pose":
                        new_rect = data["rect"]

                    # 仅在【相同类别】且【IoU > 0.8】时去重；不同类别即使重合度再高也要保留
                    is_duplicate = False
                    if new_rect:
                        for ext in existing_shapes:
                            if ext.get("label", "") == label:
                                ext_rect = get_ext_rect(ext)
                                if ext_rect and get_iou(new_rect, ext_rect) > 0.8:
                                    is_duplicate = True
                                    break

                    if is_duplicate:
                        continue

                    # 转换新预测的框为对应格式并加入队列
                    if shape_type == "rect":
                        x1, y1 = data.x(), data.y()
                        w, h = data.width(), data.height()
                        export_shapes.append({
                            "label": label,
                            "type": "rectangle",
                            "points": [[x1, y1], [x1 + w, y1 + h]]
                        })
                    elif shape_type == "poly":
                        points = [[data[i].x(), data[i].y()] for i in range(data.count())]
                        export_shapes.append({
                            "label": label,
                            "type": "polygon",
                            "points": points
                        })
                    elif shape_type == "rbox":
                        points = [[data[i].x(), data[i].y()] for i in range(data.count())]
                        pts = np.array(points, dtype=np.float32)
                        rect_obb = cv2.minAreaRect(pts)
                        cx, cy = float(rect_obb[0][0]), float(rect_obb[0][1])
                        w, h = float(rect_obb[1][0]), float(rect_obb[1][1])
                        angle = float(rect_obb[2])
                        cx, cy, w, h, angle = fit_obb_to_bounds(cx, cy, w, h, angle, (0, 0, w_img, h_img))
                        points = [[x, y] for x, y in obb_corners(cx, cy, w, h, angle)]

                        export_shapes.append({
                            "label": label,
                            "type": "obb",
                            "points": points,
                            "rect": [cx, cy, w, h],
                            "angle": angle
                        })
                    elif shape_type == "pose":
                        rect = data["rect"]
                        cx, cy = rect.x() + rect.width()/2, rect.y() + rect.height()/2
                        w, h = rect.width(), rect.height()

                        kps = data["keypoints"]
                        keypoints = []
                        for kp in kps:
                            pos = kp["pos"]
                            keypoints.append([pos.x(), pos.y(), kp["vis"]])

                        # 构造和保存模板
                        template_name = None
                        if "skeleton" in s and self.template_manager:
                            template_name = f"YOLO_Auto_{len(keypoints)}"
                            yolo_skeleton = s["skeleton"]
                            kpt_names = s.get("kpt_names", [])
                            
                            template = {
                                "name": template_name,
                                "label": label,
                                "keypoints": [],
                                "connections": []
                            }
                            
                            for i in range(len(keypoints)):
                                name = kpt_names[i] if i < len(kpt_names) else f"kp_{i}"
                                template["keypoints"].append({"name": name, "color": "#00FF00", "default_pos": [0.5, 0.5]})
                                
                            for edge in yolo_skeleton:
                                if len(edge) == 2:
                                    p1, p2 = edge[0], edge[1]
                                    if p1 > 0 and p2 > 0:
                                        template["connections"].append([p1 - 1, p2 - 1])
                                    else:
                                        template["connections"].append([p1, p2])
                                        
                            self.template_manager.add_template(template)

                        if not template_name and self.template_manager:
                            for t in self.template_manager.templates:
                                if len(t.get("keypoints", [])) == len(keypoints):
                                    template_name = t["name"]
                                    break

                        if not template_name:
                            template_name = f"YOLO_Pose_{len(keypoints)}"

                        export_shape_dict = {
                            "label": label,
                            "type": "pose",
                            "points": [],
                            "rect": [cx, cy, w, h],
                            "angle": 0.0,
                            "keypoints": keypoints,
                            "kpt_shape": [len(keypoints), 3],
                            "template_name": template_name
                        }
                        export_shapes.append(export_shape_dict)

                # 4. 后台增量保存到对应的标注文件中
                if export_shapes:
                    _protect_batch_write(target_path, expected_fingerprint)
                    if self.current_format == "json":
                        out_path = base_name + ".json"
                        Exporter.save_json(out_path, img_path, w_img, h_img, export_shapes, document_metadata)
                    elif self.current_format == "yolo":
                        out_path = base_name + ".txt"
                        Exporter.save_yolo(out_path, w_img, h_img, export_shapes, self.class_list)
                    elif self.current_format == "xml":
                        out_path = base_name + ".xml"
                        Exporter.save_xml(out_path, img_path, w_img, h_img, export_shapes)
                elif self.overwrite:
                    ext = ANNOTATION_EXTENSION_BY_FORMAT.get(self.current_format)
                    out_path = base_name + ext if ext else None
                    if out_path and os.path.exists(out_path):
                        _protect_batch_write(out_path, expected_fingerprint)
                        archive_annotation(out_path)

                processed += 1

            self.finished.emit(processed, total, list(detected_classes))

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


class BatchProgressDialog(QDialog):
    def __init__(self, parent=None, is_dark_theme=False, title="SAM 3 批量标注中"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(420, 180)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.is_cancelled = False
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        
        # Header layout (Loading.gif + text)
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)
        
        self.loading_label = QLabel()
        self.loading_label.setFixedSize(32, 32)
        self.movie = QMovie("ui/icon/Loading.gif")
        self.movie.setScaledSize(QSize(32, 32))
        self.loading_label.setMovie(self.movie)
        self.movie.start()
        
        self.status_label = QLabel("正在初始化批量标注任务...")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        
        header_layout.addWidget(self.loading_label)
        header_layout.addWidget(self.status_label, 1)
        layout.addLayout(header_layout)
        
        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #475569;
                border-radius: 6px;
                text-align: center;
                height: 20px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #22C55E;
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.progress_bar)
        
        # Cancel Button Layout
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_cancel = QPushButton("取消任务")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #EF4444;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 18px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #F87171; }
        """)
        self.btn_cancel.clicked.connect(self.cancel_task)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)
        
        if is_dark_theme:
            self.setStyleSheet("""
                QDialog { background-color: #1E293B; color: #F8FAFC; }
                QLabel { color: #F8FAFC; }
                QProgressBar { border-color: #334155; background-color: #0F172A; color: #F8FAFC; }
            """)
        else:
            self.setStyleSheet("""
                QDialog { background-color: #F8FAFC; color: #0F172A; }
                QLabel { color: #0F172A; }
                QProgressBar { border-color: #E2E8F0; background-color: #F1F5F9; color: #0F172A; }
            """)

    def cancel_task(self):
        self.is_cancelled = True
        self.status_label.setText("正在取消任务，请稍候...")
        self.btn_cancel.setEnabled(False)


class YoloClassFilterDialog(QDialog):
    def __init__(self, class_names, selected_ids, is_dark_theme=False, parent=None):
        super().__init__(parent)
        self.class_names = class_names  # dict, e.g., {0: 'person', 1: 'bicycle'}
        self.initial_selected_ids = set(selected_ids)
        self.is_dark_theme = is_dark_theme
        self.selected_ids = []
        
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(360, 520)
        
        # Colors
        bg_color = "#0F172A" if is_dark_theme else "#FFFFFF"
        border_color = "#334155" if is_dark_theme else "#E2E8F0"
        text_color = "#F8FAFC" if is_dark_theme else "#0F172A"
        list_bg = "#0F172A" if is_dark_theme else "#FFFFFF"
        item_hover_bg = "#1E293B" if is_dark_theme else "#F1F5F9"
        primary_color = "#22C55E"
        btn_hover_bg = "#16A34A"
        
        # Main Container QFrame
        self.main_container = QFrame(self)
        self.main_container.setObjectName("MainContainer")
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.main_container)
        
        # Layout inside Main Container
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(10, 10, 10, 15)
        container_layout.setSpacing(10)
        
        # Custom Title Bar
        self.title_bar = QWidget()
        self.title_bar.setObjectName("TitleBar")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 5, 5, 5)
        
        self.title_label = QLabel("YOLO 类别过滤")
        self.title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {text_color};")
        title_layout.addWidget(self.title_label)
        
        title_layout.addStretch()
        
        self.btn_close = QPushButton()
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self.reject)
        
        close_icon = QIcon("ui/icon/x.svg")
        if is_dark_theme:
            close_icon = self.set_icon_color(close_icon, QColor("#94A3B8"))
        else:
            close_icon = self.set_icon_color(close_icon, QColor("#64748B"))
        self.btn_close.setIcon(close_icon)
        self.btn_close.setIconSize(QSize(12, 12))
        
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                border: none;
                background-color: transparent;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: rgba(239, 68, 68, 0.2);
            }}
        """)
        title_layout.addWidget(self.btn_close)
        container_layout.addWidget(self.title_bar)
        
        # Search Line Edit
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索类别名称...")
        
        search_icon = QIcon("ui/icon/magnifying-glass.svg")
        if is_dark_theme:
            search_icon = self.set_icon_color(search_icon, QColor("#94A3B8"))
        else:
            search_icon = self.set_icon_color(search_icon, QColor("#64748B"))
        self.search_edit.addAction(search_icon, QLineEdit.LeadingPosition)
        
        self.search_edit.textChanged.connect(self.on_search_changed)
        container_layout.addWidget(self.search_edit)
        
        # Select All Checkbox
        self.chk_all = QCheckBox("全选 / 反选")
        self.chk_all.setCursor(Qt.PointingHandCursor)
        self.chk_all.setTristate(True)
        self.chk_all.clicked.connect(self.on_chk_all_clicked)
        container_layout.addWidget(self.chk_all)
        
        # List Widget
        self.list_widget = QListWidget()
        self.list_widget.itemChanged.connect(self.on_item_changed)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)
        container_layout.addWidget(self.list_widget)
        
        # Populate List
        self._updating_list = True
        sorted_cids = sorted(self.class_names.keys())
        for cid in sorted_cids:
            cname = self.class_names[cid]
            item = QListWidgetItem(f"{cid}: {cname}")
            item.setData(Qt.UserRole, cid)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            
            is_checked = (cid in self.initial_selected_ids)
            item.setCheckState(Qt.Checked if is_checked else Qt.Unchecked)
            self.list_widget.addItem(item)
            
        self._updating_list = False
        self.update_chk_all_state()
        
        # Bottom Buttons
        btn_layout = QHBoxLayout()
        self.btn_reset = QPushButton("重置全选")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(self.reset_to_all)
        
        self.btn_confirm = QPushButton("确认")
        self.btn_confirm.setCursor(Qt.PointingHandCursor)
        self.btn_confirm.clicked.connect(self.accept_selection)
        
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_reset)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_confirm)
        container_layout.addLayout(btn_layout)
        
        # Set Stylesheet for inner elements
        self.setStyleSheet(f"""
            QWidget {{
                background-color: transparent;
                color: {text_color};
            }}
            #MainContainer {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 12px;
            }}
            QLineEdit {{
                background-color: {list_bg};
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 8px 12px;
                color: {text_color};
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {primary_color};
            }}
            QCheckBox {{
                color: {text_color};
                font-weight: bold;
                font-size: 13px;
                spacing: 8px;
            }}
            QListWidget {{
                background-color: {list_bg};
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 6px 10px;
                border-radius: 6px;
                color: {text_color};
            }}
            QListWidget::item:hover {{
                background-color: {item_hover_bg};
            }}
            QPushButton {{
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
                background-color: {list_bg};
                color: {text_color};
            }}
            QPushButton:hover {{
                background-color: {item_hover_bg};
            }}
        """)
        
        self.btn_confirm.setStyleSheet(f"""
            QPushButton {{
                background-color: {primary_color};
                color: white;
                border: none;
            }}
            QPushButton:hover {{
                background-color: {btn_hover_bg};
            }}
        """)
        
        # Install global event filter to close on clicking outside
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Check if click is on the title bar or label
            child = self.childAt(event.position().toPoint())
            if child in [self.title_bar, self.title_label] or (child and child.parent() == self.title_bar and child != self.btn_close):
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def event(self, event):
        if event.type() == QEvent.WindowDeactivate:
            self.reject()
            return True
        return super().event(event)
            
    def on_search_changed(self, text):
        text = text.lower().strip()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text not in item.text().lower())
        self.update_chk_all_state()
            
    def on_chk_all_clicked(self):
        # Determine target state: if any visible item is currently unchecked, we want to check all.
        # Otherwise (all checked), we want to uncheck all.
        any_unchecked = False
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden() and item.checkState() == Qt.Unchecked:
                any_unchecked = True
                break
                
        self._updating_list = True
        target_state = Qt.Checked if any_unchecked else Qt.Unchecked
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden():
                item.setCheckState(target_state)
        self._updating_list = False
        self.update_chk_all_state()
        
    def on_item_changed(self, item):
        if getattr(self, '_updating_list', False):
            return
        self.update_chk_all_state()
        
    def on_item_clicked(self, item):
        from PySide6.QtGui import QCursor
        pos = self.list_widget.viewport().mapFromGlobal(QCursor.pos())
        if pos.x() > 35:
            self._updating_list = True
            new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
            item.setCheckState(new_state)
            self._updating_list = False
            self.update_chk_all_state()
            
    def on_item_double_clicked(self, item):
        self._updating_list = True
        new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        item.setCheckState(new_state)
        self._updating_list = False
        self.update_chk_all_state()
        
    def update_chk_all_state(self):
        checked_count = 0
        total_count = 0
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden():
                total_count += 1
                if item.checkState() == Qt.Checked:
                    checked_count += 1
                    
        if total_count == 0:
            self.chk_all.setCheckState(Qt.Unchecked)
        elif checked_count == total_count:
            self.chk_all.setCheckState(Qt.Checked)
        elif checked_count == 0:
            self.chk_all.setCheckState(Qt.Unchecked)
        else:
            self.chk_all.setCheckState(Qt.PartiallyChecked)
        
    def reset_to_all(self):
        self._updating_list = True
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setCheckState(Qt.Checked)
        self._updating_list = False
        self.update_chk_all_state()
        
    def accept_selection(self):
        checked_ids = []
        total_count = self.list_widget.count()
        
        for i in range(total_count):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                cid = item.data(Qt.UserRole)
                checked_ids.append(cid)
                
        if len(checked_ids) == total_count or len(checked_ids) == 0:
            self.selected_ids = []
        else:
            self.selected_ids = checked_ids
            
        self.accept()

    def set_icon_color(self, icon, color):
        from PySide6.QtGui import QPainter, QIcon
        pixmap = icon.pixmap(100, 100)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color)
        painter.end()
        new_icon = QIcon()
        new_icon.addPixmap(pixmap, QIcon.Normal, QIcon.On)
        new_icon.addPixmap(pixmap, QIcon.Normal, QIcon.Off)
        return new_icon

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.ActivationChange:
            if not self.isActiveWindow():
                self.reject()
        super().changeEvent(event)

    def cleanup_filter(self):
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.instance().removeEventFilter(self)
        except Exception:
            pass

    def reject(self):
        self.cleanup_filter()
        super().reject()

    def accept(self):
        self.cleanup_filter()
        super().accept()

    def closeEvent(self, event):
        self.cleanup_filter()
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            from PySide6.QtGui import QCursor
            if not self.geometry().contains(QCursor.pos()):
                self.reject()
        return super().eventFilter(obj, event)


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.manual_only = MANUAL_ONLY

        self.template_manager = TemplateManager()

        self.is_dark_theme = False
        self.classListWidget.set_theme(self.is_dark_theme)
        self.setStyleSheet(LIGHT_THEME)

        self.scene = Canvas(self)
        self.view.setScene(self.scene)

        self.current_image_path = None
        self.current_dir = None
        self.attribute_store = ClassAttributeStore()
        self.classListWidget.rename_guard = self._can_rename_class
        settings = QSettings("luohuabuxiema", "JotMark")
        try:
            line_width = float(settings.value("annotation_line_width", 2))
        except (ValueError, TypeError):
            line_width = 2.0
        if not 0.5 <= line_width <= 12:
            line_width = 2.0
        self.scene.set_annotation_line_width(line_width)
        show_attributes_setting = settings.value("show_canvas_attributes", True)
        self.scene.show_canvas_attributes = str(show_attributes_setting).strip().lower() not in {"0", "false", "no", "off"}
        transparent_setting = settings.value("transparent_label_background", False)
        self.scene.set_transparent_label_background(
            str(transparent_setting).strip().lower() in {"1", "true", "yes", "on"}
        )
        try:
            edge_margin = int(settings.value("edge_click_margin", 40))
        except (ValueError, TypeError):
            edge_margin = 40
        self.scene.edge_click_margin = max(0, min(160, edge_margin))
        self.annotation_output_dir = settings.value("annotation_output_dir", "") or ""
        extension_setting = settings.value("show_file_extensions", False)
        self.show_file_extensions = str(extension_setting).strip().lower() in {"1", "true", "yes", "on"}
        self.file_sort_key = str(settings.value("file_sort_key", "name") or "name")
        if self.file_sort_key not in {"name", "modified", "size"}:
            self.file_sort_key = "name"
        sort_descending_setting = settings.value("file_sort_descending", False)
        self.file_sort_descending = str(sort_descending_setting).strip().lower() in {"1", "true", "yes", "on"}
        self.btnFileExtensions.setChecked(self.show_file_extensions)
        self._update_file_extension_button()
        self._update_file_sort_button()
        self.class_list = []
        self.current_format = "json"
        self.yolo_filtered_class_ids = []
        self.sam_merge_selection_mode = False
        self.sam_merge_label = None
        self._enforcing_sam_merge_selection = False
        self._sam_image_request_id = None
        self._enable_sam_after_model_load = False

        # Coalesce high-frequency UI/state changes.  A short delay preserves
        # safety while avoiding a disk write and full tree rebuild per drag.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(600)
        self._autosave_timer.timeout.connect(self._flush_auto_save_annotation)
        self._autosave_dirty = False
        self._saved_annotation_signatures = {}
        self._annotation_disk_fingerprints = {}
        self._protected_annotation_paths = {}
        self._last_autosave_error = ""
        self._json_document_metadata = {}
        self._last_recovery_snapshot = None

        self._annotation_tree_timer = QTimer(self)
        self._annotation_tree_timer.setSingleShot(True)
        self._annotation_tree_timer.setInterval(0)
        self._annotation_tree_timer.timeout.connect(self.update_annotation_tree)

        self.modeLabel = QLabel("模式: 选择/编辑标注")
        self.statusBar.addWidget(self.modeLabel)

        self.helpLabel = QLabel("状态: 正在初始化")
        # A full CUDA/PyTorch traceback can be thousands of characters long.
        # Keep the status bar compact and expose the copyable detail in a
        # dedicated dialog instead of letting QLabel enlarge the whole window.
        self.helpLabel.setMaximumWidth(460)
        self.helpLabel.setToolTip("运行状态与错误摘要")
        self.statusBar.addWidget(self.helpLabel)

        self.saveFailureLabel = QLabel("保存失败：未保存")
        self.saveFailureLabel.setStyleSheet("color: #DC2626; font-weight: bold;")
        self.saveFailureLabel.hide()
        self.statusBar.addPermanentWidget(self.saveFailureLabel)
        self.btnRecoverySave = QPushButton("另存恢复")
        self.btnRecoverySave.setToolTip("将当前画布另存为新的 JSON 恢复文件，不覆盖原标注")
        self.btnRecoverySave.clicked.connect(self.save_recovery_snapshot)
        self.btnRecoverySave.hide()
        self.statusBar.addPermanentWidget(self.btnRecoverySave)

        self._last_runtime_error = ""
        self.btnErrorDetails = QPushButton("错误详情")
        self.btnErrorDetails.setObjectName("btnErrorDetails")
        self.btnErrorDetails.setCursor(Qt.PointingHandCursor)
        self.btnErrorDetails.setToolTip("查看并复制完整错误信息")
        self.btnErrorDetails.setStyleSheet(
            "QPushButton { color: #DC2626; font-weight: bold; border: none; padding: 0 6px; }"
            "QPushButton:hover { color: #991B1B; text-decoration: underline; }"
        )
        self.btnErrorDetails.clicked.connect(self.show_runtime_error_details)
        self.btnErrorDetails.hide()
        self.statusBar.addWidget(self.btnErrorDetails)

        self.sam_client = SAMClient(self)
        self.sam_client.inference_result.connect(self.scene.handle_sam_result)
        self.sam_client.text_result_ready.connect(self.handle_text_results)
        self.sam_client.image_ready.connect(self.on_sam_image_ready)
        self.sam_client.model_status_changed.connect(self.update_model_status)
        self.scene.sam_client = self.sam_client

        # 撤销/重做时数据栈
        self.undo_stack = []
        self.redo_stack = []
        self._undo_signatures = []
        self._redo_signatures = []
        self.max_history_steps = 20  # 保留20步历史记录
        self.scene.state_changed.connect(self.push_state)  # 绑定画板信号
        
        self._init_pose_templates()

        self._connect_signals()
        self._set_mode(CanvasMode.EDIT)
        
        if self.manual_only:
            self._configure_manual_annotation_mode()
        else:
            # Do not load SAM/YOLO at startup.  The application must stay
            # usable for manual annotation when no model file, CUDA device or
            # compatible GPU is available.
            self._init_model_selector()
            self._set_model_selector_idle_ui()
            self.btnModelSelector.show()
            self.scene.set_sam_enabled(False)
            self.helpLabel.setText("手工标注已就绪；需要智能辅助时点击“加载辅助模型”。")
            self.helpLabel.setStyleSheet("color: #16A34A;")
            if AUTOLOAD_ASSISTANT:
                if "sam3" in SAM_MODEL_MAP:
                    self.on_model_selected("sam3")
                elif SAM_MODEL_MAP:
                    self.on_model_selected(next(iter(SAM_MODEL_MAP)))
                else:
                    self.statusBar.showMessage("未发现可自动加载的辅助模型，已保持手工标注模式。", 6000)
        
        self.yolo_worker = None

        # 初始化 btnDeleteFiles 的图标颜色
        from PySide6.QtGui import QColor
        self.current_icon_color = QColor(15, 23, 42)
        self.btnDeleteFiles.setIcon(self.set_icon_color(QIcon("ui/icon/trash.svg"), self.current_icon_color))
        self.btnOverwrite.setIcon(self.set_icon_color(QIcon("ui/icon/编辑.svg"), self.current_icon_color))
        self.btnFileExtensions.setIcon(self.set_icon_color(QIcon("ui/icon/格式.svg"), self.current_icon_color))
        self.btnFileSort.setIcon(self.set_icon_color(QIcon("ui/icon/sort.svg"), self.current_icon_color))
        for button in (self.btnOverwrite, self.btnFileExtensions, self.btnFileSort):
            button.setIconSize(QSize(14, 14))
            button.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 3px; }"
                "QPushButton:hover { background: rgba(59, 130, 246, 0.16); }"
                "QPushButton:checked { background: rgba(59, 130, 246, 0.28); }"
            )
        
        self.btnPredict.setIcon(QIcon("ui/icon/lightning-fill.svg"))
        
        # 预测按钮动画
        self.predict_movie = QMovie("ui/icon/Loading.gif")
        self.predict_movie.frameChanged.connect(self.update_predict_icon)
        
        # 初始化选择框状态和可用性
        self.update_selected_count()

        # 初始化 samPromptBtn 样式与交互 (Gemini 3 Style)
        self.update_prompt_btn_icon()
        self.samPromptBtn.setEnabled(False)
        if not self.manual_only:
            self.samPromptBtn.show()
        
        # 绑定文字变化信号以实现动态状态切换
        self.samPromptInput.textChanged.connect(self.on_prompt_text_changed)
        
        # 安装事件过滤器以实现 focus 边框变化
        self.samPromptInput.installEventFilter(self)

        if str(settings.value("dark_theme", False)).lower() in ("true", "1"):
            self.toggle_theme()

        # 恢复上次关闭时的标注路径和图片
        self.restore_last_state()

    def _configure_manual_annotation_mode(self):
        """Hide AI/training controls in the lightweight manual deployment."""
        self.scene.set_sam_enabled(False)
        self.samSwitch.setChecked(False)
        self.samWidget.hide()
        self.btnSmartMode.hide()
        self.btnModelSelector.hide()
        self.btnPredict.hide()
        self.btnClassFilter.hide()
        self.samTextGroup.hide()
        self.btnDatasetTool.hide()
        self.update_sam_merge_button()
        self.helpLabel.setText("手工标注模式：无需模型、显卡或网络。")
        self.helpLabel.setStyleSheet("color: #16A34A;")

    def update_prompt_btn_icon(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon, QColor, QPainter
        active_color = QColor(255, 255, 255)
        if self.is_dark_theme:
            disabled_color = QColor(255, 255, 255, 60)
        else:
            disabled_color = QColor(100, 116, 139, 120)  # Slate gray for perfect visibility in light theme!
            
        icon = QIcon("ui/icon/arrow-up.svg")
        
        # Active Pixmap
        active_pixmap = icon.pixmap(100, 100)
        painter = QPainter(active_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(active_pixmap.rect(), active_color)
        painter.end()
        
        # Disabled Pixmap
        disabled_pixmap = icon.pixmap(100, 100)
        dpainter = QPainter(disabled_pixmap)
        dpainter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        dpainter.fillRect(disabled_pixmap.rect(), disabled_color)
        dpainter.end()
        
        new_icon = QIcon()
        new_icon.addPixmap(active_pixmap, QIcon.Normal, QIcon.On)
        new_icon.addPixmap(active_pixmap, QIcon.Normal, QIcon.Off)
        new_icon.addPixmap(disabled_pixmap, QIcon.Disabled, QIcon.On)
        new_icon.addPixmap(disabled_pixmap, QIcon.Disabled, QIcon.Off)
        
        self.samPromptBtn.setIcon(new_icon)
        self.samPromptBtn.setIconSize(QSize(16, 16))

    def update_prompt_btn_state(self):
        supports_text = self.samPromptInput.isEnabled()
        has_text = bool(self.samPromptInput.text().strip())
        self.samPromptBtn.setEnabled(supports_text and has_text)

    def on_prompt_text_changed(self, text):
        self.update_prompt_btn_state()

    def eventFilter(self, watched, event):
        if watched == getattr(self, 'samPromptInput', None):
            if event.type() == QEvent.FocusIn:
                self.samTextGroup.setProperty("focused", "true")
                self.samTextGroup.style().unpolish(self.samTextGroup)
                self.samTextGroup.style().polish(self.samTextGroup)
            elif event.type() == QEvent.FocusOut:
                self.samTextGroup.setProperty("focused", "false")
                self.samTextGroup.style().unpolish(self.samTextGroup)
                self.samTextGroup.style().polish(self.samTextGroup)
        return super().eventFilter(watched, event)

    def update_predict_icon(self):
        self.btnPredict.setIcon(QIcon(self.predict_movie.currentPixmap()))

    def _init_pose_templates(self):
        templates = self.template_manager.get_template_names()
        self.templateWidget.update_templates(templates, main_window=self)
        if not self.scene.current_pose_template:
            self.scene.current_pose_template = self.template_manager.get_template("Person (COCO)")

    def _init_model_selector(self):
        self.btnModelSelector.clicked.connect(self._show_model_selector)

    def _set_model_selector_idle_ui(self):
        """Show the explicit entry point for optional AI-assisted annotation."""
        self.btnModelSelector.setText(" 加载辅助模型 ▾")
        self.btnModelSelector.setIcon(QIcon())
        self.btnModelSelector.setToolTip(
            "按需选择并加载 SAM / YOLO 辅助模型；不加载模型也可正常手工标注"
        )

    def _sam_model_is_ready(self):
        return (
            getattr(self.sam_client, "current_model_type", "") in {"sam2", "sam3"}
            and getattr(self.sam_client, "model", None) is not None
        )

    def _assistant_is_ready(self):
        return self._sam_model_is_ready() or getattr(self, "current_yolo_predictor", None) is not None

    def _show_model_selector(self):
        if self.manual_only:
            return
        dialog = ModelSelectorDialog(current_model_key=getattr(self.sam_client, 'current_model_key', None), is_dark_theme=self.is_dark_theme, parent=self)
        dialog.model_selected.connect(self.on_model_selected)
        
        # Position popup near button
        pos = self.btnModelSelector.mapToGlobal(self.btnModelSelector.rect().bottomLeft())
        dialog.move(pos.x(), pos.y() + 5)
        dialog.exec()

    def on_model_selected(self, model_info_or_key):
        if self.manual_only:
            return
        if isinstance(model_info_or_key, str):
            key = model_info_or_key
            if key in SAM_MODEL_MAP:
                model_info = SAM_MODEL_MAP[key]
            else:
                model_info = {"key": key, "display_name": key, "type": "sam2", "supports_text": False}
        else:
            model_info = model_info_or_key
            key = model_info["key"]
            
        display_name = model_info.get("display_name", key)
        
        self.btnModelSelector.setText(f" {display_name} ▾")
        self.btnModelSelector.setIcon(QIcon("ui/icon/s.svg"))
        self.btnModelSelector.setToolTip("当前辅助模型；点击可切换或重新选择模型")
        
        if key in SAM_MODEL_MAP or model_info.get("type", "").startswith("sam"):
            self.btnPredict.hide()
            self.btnClassFilter.hide()
            self.yolo_filtered_class_ids = []
            self.current_yolo_predictor = None
            self._enable_sam_after_model_load = True
            self.scene.set_sam_enabled(False)
            if self.samSwitch.isChecked():
                self.samSwitch.setChecked(False)
            if key not in SAM_MODEL_MAP:
                SAM_MODEL_MAP[key] = model_info
            self.sam_client.load_model_by_key(key)
            # 如果当前在点标注模式（不支持SAM），自动切换回矩形标注
            if self.scene.mode == CanvasMode.POINT:
                self.actionRect.trigger()
        elif model_info.get("type", "").startswith("yolo"):
            self._enable_sam_after_model_load = False
            self.scene.set_sam_enabled(False)
            if self.samSwitch.isChecked():
                self.samSwitch.setChecked(False)
            self.sam_client.cleanup()
            self.sam_client.current_model_key = key
            from jotmark.models.yolo_predictor import YoloPredictor
            path = model_info.get("path", "")
            if path and os.path.exists(path):
                try:
                    self.current_yolo_predictor = YoloPredictor(path)
                    
                    # 自动根据 YOLO 任务类型切换绘制模式
                    task = getattr(self.current_yolo_predictor, 'task', 'detect')
                    if task == 'detect':
                        self.actionRect.trigger()
                    elif task == 'segment':
                        self.actionPoly.trigger()
                    elif task == 'pose':
                        self.actionPoint.trigger()
                    elif task == 'obb':
                        self.actionRBox.trigger()
                        
                    self.update_model_status(True, f"已加载 YOLO 模型: {display_name}")
                    self.btnPredict.show()
                    self.btnClassFilter.show()
                    self.btnClassFilter.setText(" 全部类别 ▾")
                    self.yolo_filtered_class_ids = []
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self.update_model_status(False, f"加载 YOLO 模型失败: {str(e)}")
                    self.btnPredict.hide()
                    self.btnClassFilter.hide()
                    self.yolo_filtered_class_ids = []
            else:
                self.update_model_status(False, f"未找到对应的 YOLO 模型文件")
                self.btnPredict.hide()
                self.btnClassFilter.hide()
                self.yolo_filtered_class_ids = []
        else:
            self.btnPredict.hide()
            self.btnClassFilter.hide()
            self.yolo_filtered_class_ids = []
            self.current_yolo_predictor = None
            self.sam_client.cleanup()
            self.sam_client.current_model_key = key
            self.update_model_status(True, f"已选择模型: {display_name}。非 SAM 模型暂不支持智能推理。")
        
        supports_text = model_info.get("supports_text", False)
        
        # 只有不在点标注模式下才启用，因为点标注即使是SAM3也不可用
        if self.scene.mode != CanvasMode.POINT:
            self.samPromptInput.setEnabled(supports_text)
            self.update_prompt_btn_state()
            if supports_text:
                self.samPromptInput.setPlaceholderText("输入提示词提取 (如: dog、cat)")
            else:
                self.samPromptInput.setPlaceholderText(f"{display_name} 不支持提示词")

    def edit_pose_template(self, name):
        dlg = SkeletonTemplateDialog(self, self.template_manager, self.is_dark_theme)
        dlg.load_template(name)
        dlg.name_edit.setText(name)
        if dlg.exec() == QDialog.Accepted:
            self._init_pose_templates()
            self.templateWidget._on_template_selected(name, f"{name} ▾")

    def delete_pose_template(self, name):
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(self, "删除骨架模板", f"确定要删除骨架模板 '{name}' 吗？", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.template_manager.delete_template(name)
            self._init_pose_templates()
            self.templateWidget._on_template_selected("Person (COCO)", "Person (COCO) ▾")

    def _connect_signals(self):
        self.templateWidget.edit_template.connect(self.edit_pose_template)
        self.templateWidget.delete_template.connect(self.delete_pose_template)
        self.btnCollapse.clicked.connect(self.toggle_sidebar)
        self.btnSettings.clicked.connect(self.show_settings_dialog)
        self.btnThemeToggle.clicked.connect(self.toggle_theme)

        self.btnUndo.clicked.connect(self.undo)
        self.btnRedo.clicked.connect(self.redo)
        self.btnDelete.clicked.connect(self.delete_selected)
        self.btnSave.clicked.connect(lambda: self.save_annotation(self.current_format))
        self.btnMergeSam.clicked.connect(self.merge_selected_regions)
        self.btnAnnotationOutputDir.clicked.connect(self.choose_annotation_output_dir)
        self.btnKeyboard.clicked.connect(self.show_help_dialog)

        self.actionOpen.triggered.connect(self.open_dir)

        # 下拉菜单组件信号
        self.formatWidget.format_changed.connect(self.set_current_format)

        self.btnDatasetTool.clicked.connect(self.open_dataset_tool)

        # self.actionFormatJSON.triggered.connect(lambda: self.set_current_format("json"))
        # self.actionFormatYOLO.triggered.connect(lambda: self.set_current_format("yolo"))
        # self.actionFormatXML.triggered.connect(lambda: self.set_current_format("xml"))

        self.actionSelect.triggered.connect(lambda checked=False: self._set_mode(CanvasMode.EDIT))
        self.actionRect.triggered.connect(lambda checked=False: self._set_mode(CanvasMode.RECT))
        self.actionPoly.triggered.connect(lambda checked=False: self._set_mode(CanvasMode.POLY))
        self.actionLine.triggered.connect(lambda checked=False: self._set_mode(CanvasMode.LINE))
        self.actionPoint.triggered.connect(lambda checked=False: self._set_mode(CanvasMode.POINT))
        self.actionRBox.triggered.connect(lambda checked=False: self._set_mode(CanvasMode.RBOX))

        self.templateWidget.template_changed.connect(self.on_pose_template_changed)

        self.samSwitch.toggled.connect(self.on_sam_toggled)

        # 同步顶部 Draw / Smart 按钮与 SAM 开关状态
        self.btnDrawMode.toggled.connect(lambda checked: self.samSwitch.setChecked(False) if checked else None)
        self.btnSmartMode.toggled.connect(lambda checked: self.samSwitch.setChecked(True) if checked else None)

        self.btnPredict.clicked.connect(self.on_predict_clicked)
        self.btnClassFilter.clicked.connect(self.on_class_filter_clicked)

        self.samPromptBtn.clicked.connect(self.trigger_sam_prompt)
        self.samPromptInput.returnPressed.connect(self.trigger_sam_prompt)

        self.chkSelectAll.stateChanged.connect(self.on_select_all_toggled)
        self.btnDeleteFiles.clicked.connect(self.delete_checked_files)
        self.btnFileExtensions.toggled.connect(self.set_file_extension_visible)
        self.btnFileSort.clicked.connect(self.show_file_sort_menu)
        self.btnPreviousImage.clicked.connect(self.show_previous_image)
        self.btnNextImage.clicked.connect(self.show_next_image)
        self.listFiles.itemChanged.connect(self.on_file_item_changed)
        self.listFiles.currentItemChanged.connect(self.on_file_selected)
        self.listFiles.customContextMenuRequested.connect(self.show_file_list_context_menu)
        self.scene.mouse_moved.connect(self.update_coordinate_label)
        self.scene.shape_drawn.connect(self.handle_new_shape)
        self.scene.shape_label_clicked.connect(self.on_shape_label_clicked)
        self.scene.shape_attribute_clicked.connect(self.on_canvas_attribute_clicked)
        self.scene.shape_attributes_summary_clicked.connect(self.on_canvas_attributes_summary_clicked)
        self.scene.merge_selection_cancel_requested.connect(self.cancel_region_merge_selection)

        # These window-level shortcuts work even when the canvas has focus.
        self.previous_image_shortcut = QShortcut(QKeySequence(Qt.Key_PageUp), self)
        self.previous_image_shortcut.setContext(Qt.WindowShortcut)
        self.previous_image_shortcut.activated.connect(self.show_previous_image)
        self.next_image_shortcut = QShortcut(QKeySequence(Qt.Key_PageDown), self)
        self.next_image_shortcut.setContext(Qt.WindowShortcut)
        self.next_image_shortcut.activated.connect(self.show_next_image)

        self.update_annotation_output_dir_ui()

        # Annotation tree dual-sync signals
        self.scene.state_changed.connect(self.schedule_annotation_tree_refresh)
        self.scene.state_changed.connect(self.auto_save_annotation)
        self.scene.selectionChanged.connect(self.enforce_sam_merge_selection)
        self.scene.selectionChanged.connect(self.sync_selection_to_tree)
        self.scene.selectionChanged.connect(self.update_attribute_editor)
        self.scene.selectionChanged.connect(self.update_sam_merge_button)
        self.scene.canvas_item_hovered.connect(self.classListWidget.highlight_item_by_shape)

        self.scene.shape_double_clicked.connect(self.edit_shape_label)  # 双击修改

        # 类别管理组件信号
        self.classListWidget.class_added.connect(self._on_class_added_from_widget)
        self.classListWidget.class_renamed.connect(self._on_class_renamed_from_widget)
        self.classListWidget.color_changed.connect(self.on_class_color_changed)
        self.classListWidget.item_changed.connect(self.on_list_item_changed)
        self.classListWidget.shape_class_reassigned.connect(self.on_shape_class_reassigned)
        self.classListWidget.class_delete_requested.connect(self.delete_class_and_annotations)
        self.classListWidget.class_file_import_requested.connect(self.import_class_file)
        self.classListWidget.attribute_file_import_requested.connect(self.import_attribute_file)
        self.classListWidget.category_conversion_requested.connect(self.open_category_conversion)
        self.classListWidget.manage_attributes_requested.connect(self.manage_class_attributes)
        self.classListWidget.manage_global_attributes_requested.connect(self.manage_global_attributes)
        self.classListWidget.annotation_shape_selected.connect(lambda _shape: self.update_attribute_editor())
        self.attributeEditor.attributes_changed.connect(self.on_shape_attributes_changed)
        self.attributeEditor.attribute_label_edit_requested.connect(self.rename_attribute_label)
        self.attributeEditor.class_label_changed.connect(self.on_shape_class_reassigned)

    def on_predict_clicked(self):
        if not self._ensure_pending_annotation_saved():
            return
        if self.current_format == "curvelanes":
            DialogOver(
                self,
                "CurveLanes 格式仅保存手工绘制的开放折线；YOLO 预测结果不能转换为车道线标注。请切换到折线工具手工标注。",
                "该格式不支持自动预测",
                "warning",
            )
            return
        if not getattr(self, 'current_yolo_predictor', None):
            DialogOver(self, "YOLO 模型未加载或初始化失败！", "提示", "warning")
            return

        if self.yolo_worker and self.yolo_worker.isRunning():
            return

        # 获取当前勾选的所有图片路径
        checked_paths = []
        for i in range(self.listFiles.count()):
            item = self.listFiles.item(i)
            if item and item.checkState() == Qt.Checked:
                checked_paths.append(self._file_item_path(item))

        if checked_paths:
            # YOLO 批量预测逻辑
            self.statusBar.showMessage(f"正在进行 YOLO 批量预测 ({len(checked_paths)}张图片)...", 3000)
            self.batch_dialog = BatchProgressDialog(self, self.is_dark_theme, "YOLO 批量预测中")
            
            self.yolo_worker = YoloBatchPredictorWorker(
                predictor=self.current_yolo_predictor,
                img_paths=checked_paths,
                current_format=self.current_format,
                class_list=self.class_list,
                template_manager=self.template_manager,
                classes=self.yolo_filtered_class_ids,
                overwrite=self.btnOverwrite.isChecked(),
                annotation_output_dir=self.annotation_output_dir,
            )

            # 连接进度更新
            def update_progress(current, total, filename):
                percent = int((current / total) * 100) if total > 0 else 0
                self.batch_dialog.progress_bar.setValue(percent)
                self.batch_dialog.status_label.setText(f"正在预测 ({current + 1}/{total}):\n{filename}")
                
            self.yolo_worker.progress.connect(update_progress)

            # 完成回调
            def on_batch_finished(processed, total, detected_classes):
                self.batch_dialog.movie.stop()
                self.batch_dialog.accept()
                
                # 同步新增的类别到历史面板
                added_classes = []
                for cls in detected_classes:
                    if cls not in self.class_list:
                        self.add_class_to_list(cls)
                        added_classes.append(cls)
                if added_classes:
                    self.save_classes()

                # 如果当前打开的图片刚好在批量预测的列表中，重新加载以即时渲染预测标注
                if self.current_image_path in checked_paths:
                    if not self._ensure_pending_annotation_saved():
                        return
                    self.scene.clear_shapes()
                    self.load_annotations(self.current_image_path)
                    self.apply_class_colors_to_scene()
                    self.update_annotation_tree()
                    self._remember_current_annotation_signature()
                    self.push_state()

                self.update_annotation_status()

                DialogOver(self, f"YOLO 批量预测完成！已成功处理 {processed}/{total} 张图片并自动保存。", "批量预测成功", "success")
                self.statusBar.showMessage(f"批量预测完成！处理了 {processed} 张图片", 5000)

            # 错误回调
            def on_batch_error(err_msg):
                self.batch_dialog.movie.stop()
                self.batch_dialog.reject()
                DialogOver(self, f"批量预测出错: {err_msg}", "预测失败", "danger")
                self.statusBar.showMessage("批量预测出错", 3000)

            self.yolo_worker.finished.connect(on_batch_finished)
            self.yolo_worker.error.connect(on_batch_error)
            
            # 取消按钮连接
            self.batch_dialog.btn_cancel.clicked.connect(lambda: setattr(self.yolo_worker, 'is_cancelled', True))

            self.yolo_worker.start()
            self.batch_dialog.exec()
        else:
            # 默认单张图片预测逻辑
            if not self.current_image_path:
                DialogOver(self, "请先打开一张图片！", "提示", "warning")
                return
                
            self.statusBar.showMessage("正在使用 YOLO 进行预测...", 3000)
            self.helpLabel.setText("正在使用 YOLO 进行预测...")
            self.helpLabel.setStyleSheet("color: orange;")
            self.original_predict_text = self.btnPredict.text()
            self.btnPredict.setText("预测中")
            
            self.predict_movie.start()
            self.btnPredict.setEnabled(False)
            
            self.yolo_worker = YoloPredictorWorker(self.current_yolo_predictor, self.current_image_path, classes=self.yolo_filtered_class_ids)
            self.yolo_worker.finished.connect(self.on_predict_finished)
            self.yolo_worker.error.connect(self.on_predict_error)
            self.yolo_worker.start()

    def on_predict_finished(self, shapes):
        self.predict_movie.stop()
        self.btnPredict.setText(self.original_predict_text)
        self.btnPredict.setIcon(QIcon("ui/icon/lightning-fill.svg"))
        self.btnPredict.setEnabled(True)
        
        if self.btnOverwrite.isChecked():
            if not self._ensure_pending_annotation_saved():
                return
            self.scene.clear_shapes()
            self.update_annotation_tree()
            
        if not shapes:
            if self.btnOverwrite.isChecked():
                self.auto_save_annotation()
                self.push_state()
            DialogOver(self, "未找到任何预测结果", "提示", "info")
            self.statusBar.showMessage("预测完成，未找到任何结果", 3000)
            self._update_help_text(self.scene.mode)
            return
            
        # Get existing shapes for deduplication
        existing_shapes = []
        if not self.btnOverwrite.isChecked():
            for item in self.scene.items():
                from jotmark.graphics.shapes import BaseShape
                if isinstance(item, BaseShape) and not getattr(item, 'is_temp', False):
                    existing_shapes.append(item)
            
        # Add shapes
        class_counts = {}
        added_count = 0
        
        for s in shapes:
            shape_type = s["type"]
            label = s["label"]
            data = s["data"]
            
            # Deduplication logic (IoU > 0.8)
            new_rect = None
            if shape_type == "rect":
                new_rect = data
            elif shape_type in ["poly", "rbox"]:
                new_rect = data.boundingRect()
            elif shape_type == "pose":
                new_rect = data["rect"]
                
            is_duplicate = False
            if new_rect:
                for ext_shape in existing_shapes:
                    if getattr(ext_shape, 'label', '') != label:
                        continue
                    ext_rect = ext_shape.sceneBoundingRect()
                    
                    intersection = new_rect.intersected(ext_rect)
                    inter_area = max(0, intersection.width()) * max(0, intersection.height())
                    area1 = max(0, new_rect.width()) * max(0, new_rect.height())
                    area2 = max(0, ext_rect.width()) * max(0, ext_rect.height())
                    union_area = area1 + area2 - inter_area
                    
                    if union_area > 0 and (inter_area / union_area) > 0.8:
                        is_duplicate = True
                        break
                        
            if is_duplicate:
                continue
            
            added_count += 1
            class_counts[label] = class_counts.get(label, 0) + 1
            
            if label not in self.class_list:
                self.add_class_to_list(label)
                
            new_shape = None
            if shape_type == "rect":
                new_shape = RectShape(data, label)
            elif shape_type == "poly" or shape_type == "rbox":
                new_shape = PolyShape(data, label)
            elif shape_type == "pose":
                rect = data["rect"]
                kps = data["keypoints"]
                
                # 优先使用模型预测结果中自带的骨架连接逻辑 (YOLO 官方定义)
                if "skeleton" in s:
                    yolo_skeleton = s["skeleton"]
                    kpt_names = s.get("kpt_names", [])
                    
                    # 构造临时模板，直接遵循 YOLO 官网/模型的连接定义
                    template = {
                        "name": f"YOLO_Auto_{len(kps)}",
                        "label": label,
                        "keypoints": [],
                        "connections": []
                    }
                    
                    # 填充点名称
                    for i in range(len(kps)):
                        name = kpt_names[i] if i < len(kpt_names) else f"kp_{i}"
                        template["keypoints"].append({"name": name, "color": "#00FF00", "default_pos": [0.5, 0.5]})
                        
                    # 填充连接线 (YOLO 官网通常是 1-based 索引，需要转为 0-based)
                    for edge in yolo_skeleton:
                        if len(edge) == 2:
                            p1, p2 = edge[0], edge[1]
                            # 自动检测并转换 1-based 到 0-based
                            if p1 > 0 and p2 > 0:
                                template["connections"].append([p1 - 1, p2 - 1])
                            else:
                                template["connections"].append([p1, p2])
                                
                    # 保存动态构建的骨架模板到本地模板库，以便再次打开时完美连线
                    self.template_manager.add_template(template)
                else:
                    # 兜底方案：尝试从本地模板库匹配
                    template = self.scene.current_pose_template
                    if not template or len(template.get("keypoints", [])) != len(kps):
                        found_template = False
                        for t in self.template_manager.templates:
                            if len(t.get("keypoints", [])) == len(kps):
                                template = t
                                found_template = True
                                break
                        
                        if not found_template:
                            template = {"name": f"YOLO_Pose_{len(kps)}", "keypoints": [], "connections": []}
                            for i in range(len(kps)):
                                template["keypoints"].append({"name": f"kp_{i}", "color": "#00FF00", "default_pos": [0.5, 0.5]})
                        
                new_shape = PoseShape(rect, template, label)
                for i, kp in enumerate(kps):
                    if i < len(new_shape.kps):
                        local_pt = new_shape.mapFromScene(kp["pos"])
                        new_shape.kps[i].setPos(local_pt)
                        new_shape.kps[i].set_visibility(kp["vis"])
                
                new_shape.update_bounding_box()
                new_shape.update_lines()
                
            if new_shape:
                self.scene.addItem(new_shape)
                if hasattr(new_shape, 'update_label_text'):
                    new_shape.update_label_text(label)
                if hasattr(new_shape, 'update_label_position'):
                    new_shape.update_label_position(new_shape)
                if hasattr(new_shape, 'set_color'):
                    new_shape.set_color(self.classListWidget.get_class_color(label))
                    
        if added_count == 0:
            DialogOver(self, "暂无新的预测内容需要添加", "提示", "info")
            self.statusBar.showMessage("预测完成，暂无新内容", 3000)
            self._update_help_text(self.scene.mode)
            return
        
        self.save_classes()
        self.push_state()
        self.auto_save_annotation()
        
        # Format message
        msg_parts = [f"{cls} ({count})" for cls, count in class_counts.items()]
        msg = "添加了" + str(added_count) + "个目标：" + ", ".join(msg_parts)
        DialogOver(self, msg + "，并进行标注", "预测成功", "success")
        self.statusBar.showMessage(msg, 5000)
        self._update_help_text(self.scene.mode)

    def on_predict_error(self, err_msg):
        self.predict_movie.stop()
        self.btnPredict.setText(self.original_predict_text)
        self.btnPredict.setIcon(QIcon("ui/icon/lightning-fill.svg"))
        self.btnPredict.setEnabled(True)
        DialogOver(self, f"预测失败: {err_msg}", "错误", "danger")
        self.statusBar.showMessage("预测出错", 3000)
        self._update_help_text(self.scene.mode)

    def on_class_filter_clicked(self):
        if not getattr(self, 'current_yolo_predictor', None):
            DialogOver(self, "YOLO 模型未加载或初始化失败！", "提示", "warning")
            return
            
        model = self.current_yolo_predictor.model
        if not hasattr(model, 'names') or not model.names:
            DialogOver(self, "未能从当前 YOLO 模型中提取类别列表！", "提示", "warning")
            return
            
        dialog = YoloClassFilterDialog(
            class_names=model.names,
            selected_ids=self.yolo_filtered_class_ids,
            is_dark_theme=self.is_dark_theme,
            parent=self
        )
        
        # Position dialog right below the btnClassFilter button
        try:
            pos = self.btnClassFilter.mapToGlobal(self.btnClassFilter.rect().bottomLeft())
            # Align the dialog center-ish with the button (offsetting x by -60 pixels)
            dialog.move(pos.x() - 60, pos.y() + 5)
        except Exception as e:
            print(f"Failed to position class filter dialog: {e}")
            
        if dialog.exec() == QDialog.Accepted:
            self.yolo_filtered_class_ids = dialog.selected_ids
            # Update button text dynamically
            if not self.yolo_filtered_class_ids:
                self.btnClassFilter.setText(" 全部类别 ▾")
            elif len(self.yolo_filtered_class_ids) == 1:
                cid = self.yolo_filtered_class_ids[0]
                cname = model.names[cid]
                self.btnClassFilter.setText(f" {cname} ▾")
            else:
                self.btnClassFilter.setText(f" 已选 {len(self.yolo_filtered_class_ids)} 类 ▾")

    def _on_class_added_from_widget(self, cls_name):
        """从 ClassListWidget 添加新类别的回调"""
        if cls_name not in self.class_list:
            self.class_list.append(cls_name)
            self.save_classes()

    def _can_modify_attribute_schema(self):
        if not self.attribute_store.load_error:
            return True
        QMessageBox.warning(
            self, "属性配置受保护",
            "属性配置读取失败，暂不能修改类别或属性，以免覆盖原文件。请先备份并修复配置，"
            "或通过“属性导入”导入有效配置。\n\n" + self.attribute_store.load_error,
        )
        return False

    def _backup_category_operation(self):
        """Snapshot category metadata and sidecars before a dataset-wide change."""
        project = self.current_dir or (os.path.dirname(self.current_image_path) if self.current_image_path else "")
        annotations = self.annotation_output_dir or project
        roots = []
        for label, directory in (("project", project), ("annotations", annotations)):
            if directory and os.path.isdir(directory):
                absolute = os.path.abspath(directory)
                if all(existing != absolute for _, existing in roots):
                    roots.append((label, absolute))
        if not roots:
            return ""
        backup_root = os.path.join(roots[0][1], ".jotmark_backups")
        os.makedirs(backup_root, exist_ok=True)
        backup = tempfile.mkdtemp(prefix="category-change-", dir=backup_root)
        for label, directory in roots:
            destination = os.path.join(backup, label)
            os.makedirs(destination, exist_ok=True)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_file() and entry.name.lower().endswith((".json", ".xml", ".txt")):
                        target = os.path.join(destination, entry.name)
                        shutil.copy2(entry.path, target)
                        if file_fingerprint(entry.path) != file_fingerprint(target):
                            raise OSError(f"备份期间文件发生变化：{entry.path}")
        return backup

    def _dataset_image_stems(self):
        directory = self.current_dir or (os.path.dirname(self.current_image_path) if self.current_image_path else "")
        return {os.path.normcase(os.path.splitext(os.path.basename(path))[0])
                for path in self._directory_image_paths(directory)}

    def _verify_known_annotation_revision(self, path):
        key = os.path.abspath(path)
        if key in self._protected_annotation_paths:
            raise RuntimeError(f"原标注读取不完整，禁止修改：{path}")
        if key in self._annotation_disk_fingerprints and file_fingerprint(path) != self._annotation_disk_fingerprints[key]:
            raise RuntimeError(f"标注文件已被外部修改：{path}")

    def _remember_known_annotation_revision(self, path):
        key = os.path.abspath(path)
        if key in self._annotation_disk_fingerprints:
            self._annotation_disk_fingerprints[key] = file_fingerprint(path)

    def _protect_dataset_sidecar_write(self, path, read_fingerprint):
        """Protect both loaded and unopened sidecars during bulk edits."""
        self._verify_known_annotation_revision(path)
        if file_fingerprint(path) != read_fingerprint:
            raise RuntimeError(f"读取后标注文件被外部修改：{path}")
        backup_previous_annotation(path)
        if file_fingerprint(path) != read_fingerprint:
            raise RuntimeError(f"备份期间标注文件被外部修改：{path}")

    def _can_rename_class(self, _old_name, _new_name):
        if not self._can_modify_attribute_schema() or not self._ensure_pending_annotation_saved():
            return False
        try:
            self._last_category_backup = self._backup_category_operation()
            self.attribute_store.rename_class(_old_name, _new_name)
            try:
                self.attribute_store.save()
            except Exception:
                self.attribute_store.rename_class(_new_name, _old_name)
                raise
            return True
        except Exception as exc:
            QMessageBox.warning(self, "类别修改已取消", f"备份或属性配置写入失败，未修改类别：\n{exc}")
            return False

    def _on_class_renamed_from_widget(self, old_name, new_name):
        """从 ClassListWidget 重命名类别的回调"""
        if old_name in self.class_list:
            failures = []
            idx = self.class_list.index(old_name)
            self.class_list[idx] = new_name
            # 遍历画板更新形状标签
            changed = False
            for shape in self.scene.items():
                if isinstance(shape, (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape)):
                    if getattr(shape, 'label', '') == old_name:
                        shape.label = new_name
                        if hasattr(shape, 'update_label_text'):
                            shape.update_label_text(new_name)
                        changed = True
            try:
                self.save_classes()
            except Exception as exc:
                failures.append(f"类别文件保存失败：{exc}")
            
            # 批量更新当前标注输出目录下的其他标注文件 (json, xml)
            annotation_dir = self.annotation_output_dir or self.current_dir
            image_stems = self._dataset_image_stems()
            if annotation_dir:
                try:
                    import glob
                    import xml.etree.ElementTree as ET
                    import os, json
                    
                    # 更新 JSON
                    for json_file in glob.glob(os.path.join(annotation_dir, "*.json")):
                        if (os.path.basename(json_file) in {"class_colors.json", "class_attributes.json"}
                                or os.path.normcase(os.path.splitext(os.path.basename(json_file))[0]) not in image_stems):
                            continue
                        modified_file = False
                        try:
                            read_fingerprint = file_fingerprint(json_file)
                            with open(json_file, 'r', encoding='utf-8') as f:
                                data = json.load(f, parse_constant=_reject_json_constant)
                            if 'shapes' in data:
                                for s in data['shapes']:
                                    if s.get('label') == old_name:
                                        s['label'] = new_name
                                        modified_file = True
                            if modified_file:
                                self._protect_dataset_sidecar_write(json_file, read_fingerprint)
                                Exporter._atomic_write_text(json_file, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
                                self._remember_known_annotation_revision(json_file)
                        except Exception as exc:
                            failures.append(f"{json_file}: {exc}")
                            
                    # 更新 XML
                    for xml_file in glob.glob(os.path.join(annotation_dir, "*.xml")):
                        if os.path.normcase(os.path.splitext(os.path.basename(xml_file))[0]) not in image_stems:
                            continue
                        modified_file = False
                        try:
                            read_fingerprint = file_fingerprint(xml_file)
                            tree = ET.parse(xml_file)
                            root = tree.getroot()
                            for obj in root.findall('object'):
                                name_node = obj.find('name')
                                if name_node is not None and name_node.text == old_name:
                                    name_node.text = new_name
                                    modified_file = True
                            if modified_file:
                                self._protect_dataset_sidecar_write(xml_file, read_fingerprint)
                                xml_text = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")
                                Exporter._atomic_write_text(xml_file, xml_text)
                                self._remember_known_annotation_revision(xml_file)
                        except Exception as exc:
                            failures.append(f"{xml_file}: {exc}")
                except Exception as e:
                    failures.append(str(e))
            
            if changed:
                # Keep AnnotationTreeWidget's live-shape cache aligned with
                # the new labels before any later class/theme rebuild.  Without
                # this immediate refresh, a renamed category can temporarily
                # group cached rows under the old name and leave blank/missing
                # annotation entries on the right panel.
                self.update_annotation_tree()
                self.update_attribute_editor()
                self.auto_save_annotation()
                self.push_state()
            if failures:
                DialogOver(self, f"类别已修改，但部分文件未更新，请检查并使用备份恢复：\n{getattr(self, '_last_category_backup', '')}\n\n" + "\n".join(failures), "部分更新失败", "warning")
            else:
                DialogOver(self, f"已将 '{old_name}' 变更为 '{new_name}'。\n备份：{getattr(self, '_last_category_backup', '')}", "修改成功", "success")

    def _delete_class_from_saved_annotations(self, class_name, removed_class_index, annotation_dir):
        """Delete one class from JSON, VOC XML and YOLO files in an output directory.

        YOLO stores a class index rather than a class name, so labels after the
        removed index are shifted down by one to stay aligned with classes.txt.
        """
        stats = {"json_shapes": 0, "xml_objects": 0, "yolo_lines": 0, "files": 0, "errors": []}
        if not annotation_dir or not os.path.isdir(annotation_dir):
            return stats

        import glob
        import xml.etree.ElementTree as ET
        image_stems = self._dataset_image_stems()

        for json_file in glob.glob(os.path.join(annotation_dir, "*.json")):
            if (os.path.basename(json_file) in {"class_colors.json", "class_attributes.json"}
                    or os.path.normcase(os.path.splitext(os.path.basename(json_file))[0]) not in image_stems):
                continue
            try:
                read_fingerprint = file_fingerprint(json_file)
                with open(json_file, "r", encoding="utf-8") as stream:
                    data = json.load(stream, parse_constant=_reject_json_constant)
                shapes = data.get("shapes") if isinstance(data, dict) else None
                if not isinstance(shapes, list):
                    continue
                filtered = [shape for shape in shapes if shape.get("label") != class_name]
                removed = len(shapes) - len(filtered)
                if not removed:
                    continue
                data["shapes"] = filtered
                self._protect_dataset_sidecar_write(json_file, read_fingerprint)
                Exporter._atomic_write_text(json_file, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
                self._remember_known_annotation_revision(json_file)
                stats["json_shapes"] += removed
                stats["files"] += 1
            except Exception as exc:
                print(f"删除 JSON 类别标注失败: {json_file}, {exc}")
                stats["errors"].append(f"{json_file}: {exc}")

        for xml_file in glob.glob(os.path.join(annotation_dir, "*.xml")):
            if os.path.normcase(os.path.splitext(os.path.basename(xml_file))[0]) not in image_stems:
                continue
            try:
                read_fingerprint = file_fingerprint(xml_file)
                tree = ET.parse(xml_file)
                root = tree.getroot()
                removed = 0
                for obj in list(root.findall("object")):
                    if obj.findtext("name") == class_name:
                        root.remove(obj)
                        removed += 1
                if not removed:
                    continue
                xml_text = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")
                self._protect_dataset_sidecar_write(xml_file, read_fingerprint)
                Exporter._atomic_write_text(xml_file, xml_text)
                self._remember_known_annotation_revision(xml_file)
                stats["xml_objects"] += removed
                stats["files"] += 1
            except Exception as exc:
                print(f"删除 XML 类别标注失败: {xml_file}, {exc}")
                stats["errors"].append(f"{xml_file}: {exc}")

        for yolo_file in glob.glob(os.path.join(annotation_dir, "*.txt")):
            if (os.path.basename(yolo_file) == "classes.txt"
                    or os.path.normcase(os.path.splitext(os.path.basename(yolo_file))[0]) not in image_stems):
                continue
            try:
                read_fingerprint = file_fingerprint(yolo_file)
                with open(yolo_file, "r", encoding="utf-8") as stream:
                    source_lines = stream.read().splitlines()
                target_lines = []
                removed = 0
                changed = False
                for source_line in source_lines:
                    parts = source_line.split()
                    if not parts:
                        continue
                    try:
                        class_id = int(parts[0])
                    except ValueError:
                        target_lines.append(source_line)
                        continue
                    if class_id == removed_class_index:
                        removed += 1
                        changed = True
                        continue
                    if class_id > removed_class_index:
                        parts[0] = str(class_id - 1)
                        target_lines.append(" ".join(parts))
                        changed = True
                    else:
                        target_lines.append(source_line)
                if not changed:
                    continue
                self._protect_dataset_sidecar_write(yolo_file, read_fingerprint)
                Exporter._atomic_write_text(yolo_file, "\n".join(target_lines))
                self._remember_known_annotation_revision(yolo_file)
                stats["yolo_lines"] += removed
                stats["files"] += 1
            except Exception as exc:
                print(f"删除 YOLO 类别标注失败: {yolo_file}, {exc}")
                stats["errors"].append(f"{yolo_file}: {exc}")
        return stats

    def delete_class_and_annotations(self, class_name):
        """Delete a category, its schemas, and all matching annotations in scope."""
        if not self._can_modify_attribute_schema():
            return
        class_name = (class_name or "").strip()
        if class_name not in self.class_list:
            return

        removed_class_index = self.class_list.index(class_name)
        shape_types = (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape)
        shapes_to_remove = [
            item for item in self.scene.items()
            if isinstance(item, shape_types)
            and not getattr(item, "is_temp", False)
            and getattr(item, "label", "") == class_name
        ]
        annotation_dir = self.annotation_output_dir or self.current_dir
        scope_text = (
            f"\n\n还会删除“{annotation_dir}”内 JSON、XML、YOLO 文件中该类别的标注；"
            "YOLO 中后续类别编号会自动前移。"
            if annotation_dir else ""
        )
        dialog = QMessageBox(self)
        dialog.setWindowTitle("删除类别及全部标注")
        dialog.setIcon(QMessageBox.Warning)
        dialog.setText(f"即将删除类别“{class_name}”。当前图片中有 {len(shapes_to_remove)} 个同类标注。")
        dialog.setInformativeText(
            f"该类别的专属属性定义也会删除。{scope_text}\n\n"
            "此操作不能通过撤销恢复。请先按 Ctrl+S 保存当前工作，并备份图片、JSON、"
            "class_attributes.json 后再继续。"
        )
        continue_button = dialog.addButton("我已确认，继续删除", QMessageBox.DestructiveRole)
        dialog.addButton("取消", QMessageBox.RejectRole)
        dialog.exec()
        if dialog.clickedButton() != continue_button:
            return

        if not self._ensure_pending_annotation_saved():
            return
        try:
            backup_dir = self._backup_category_operation()
        except Exception as exc:
            QMessageBox.warning(self, "删除已取消", f"自动备份失败，未删除类别：\n{exc}")
            return

        old_attributes = self.attribute_store.attributes_for(class_name)
        try:
            self.attribute_store.remove_class(class_name)
            self.attribute_store.save()
        except Exception as exc:
            self.attribute_store.set_attributes(class_name, old_attributes)
            QMessageBox.warning(self, "删除已取消", f"属性配置写入失败，未删除类别：\n{exc}")
            return

        disk_stats = self._delete_class_from_saved_annotations(
            class_name, removed_class_index, annotation_dir
        )
        for shape in shapes_to_remove:
            self.scene.removeItem(shape)

        self.class_list.remove(class_name)
        self.classListWidget.remove_class(class_name)
        try:
            self.save_classes()
        except Exception as exc:
            disk_stats["errors"].append(f"类别文件保存失败：{exc}")

        # A class deletion also rewrites saved data outside the current canvas;
        # local undo snapshots cannot safely restore those files or its schema.
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._undo_signatures.clear()
        self._redo_signatures.clear()
        self.update_undo_redo_buttons()
        self.update_annotation_tree()
        self.update_attribute_editor()
        if not self.auto_save_annotation(immediate=True):
            disk_stats["errors"].append("当前画布保存失败：" + self._last_autosave_error)

        message = (
            f"已删除类别“{class_name}”：当前图片 {len(shapes_to_remove)} 个标注，"
            f"JSON {disk_stats['json_shapes']} 个，XML {disk_stats['xml_objects']} 个，"
            f"YOLO {disk_stats['yolo_lines']} 行。\n自动备份：{backup_dir or '无已保存文件'}"
        )
        if disk_stats["errors"]:
            DialogOver(self, message + "\n部分文件修改失败，请检查并使用备份恢复：\n" + "\n".join(disk_stats["errors"]), "部分更新失败", "warning")
        else:
            DialogOver(self, message, "类别已删除", "success")

    def on_class_color_changed(self, cls_name, color):
        """类别颜色变更时，同步更新画布上所有该类别的形状颜色"""
        from PySide6.QtGui import QColor as _QC
        if not isinstance(color, _QC):
            color = _QC(color)
        for item in self.scene.items():
            if isinstance(item, (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape)):
                if getattr(item, 'label', '') == cls_name and hasattr(item, 'set_color'):
                    item.set_color(color)

    def add_class_to_list(self, cls_name):
        """列表项支持双击编辑"""
        if cls_name not in self.class_list:
            self.class_list.append(cls_name)
            self.classListWidget.add_class(cls_name)

    def push_state(self):
        """把当前画布状态拍个快照，存进撤销堆栈"""
        if not self.current_image_path: return
        current_state = Exporter.extract_shapes(self.scene)
        current_signature = self._state_signature(current_state)

        # States are already needed for undo, but cache their compact signature
        # so every mouse release does not serialize the previous full snapshot.
        if self._undo_signatures and self._undo_signatures[-1] == current_signature:
            return

        self.undo_stack.append(current_state)
        self._undo_signatures.append(current_signature)
        # 限制最大步数
        if len(self.undo_stack) > self.max_history_steps:
            self.undo_stack.pop(0)
            self._undo_signatures.pop(0)

        # 一旦有新操作，重做（前进）堆栈必须清空
        self.redo_stack.clear()
        self._redo_signatures.clear()
        self.update_undo_redo_buttons()

    @staticmethod
    def _state_signature(state):
        return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def undo(self):
        """撤销：多边形绘制中撤销顶点，其他情况撤销整步操作"""
        from jotmark.graphics.canvas import CanvasMode
        # 多边形绘制中：撤销最后一个顶点
        if self.scene.mode in (CanvasMode.POLY, CanvasMode.LINE) and len(self.scene.poly_pts) > 0:
            removed_pt = self.scene.poly_pts.pop()
            if not hasattr(self, '_poly_redo_pts'):
                self._poly_redo_pts = []
            self._poly_redo_pts.append(removed_pt)
            self.scene.update_temp_poly()
            self.update_undo_redo_buttons()
            return

        # 常规撤销：恢复上一步画布状态
        if len(self.undo_stack) > 1:
            current_state = self.undo_stack.pop()
            current_signature = self._undo_signatures.pop()
            self.redo_stack.append(current_state)
            self._redo_signatures.append(current_signature)
            previous_state = self.undo_stack[-1]
            self.restore_state(previous_state)
            self.update_undo_redo_buttons()

    def redo(self):
        """重做：多边形绘制中恢复顶点，其他情况前进一步"""
        from jotmark.graphics.canvas import CanvasMode
        # 多边形绘制中：恢复被撤销的顶点
        if self.scene.mode in (CanvasMode.POLY, CanvasMode.LINE) and hasattr(self, '_poly_redo_pts') and self._poly_redo_pts:
            pt = self._poly_redo_pts.pop()
            self.scene.poly_pts.append(pt)
            self.scene.update_temp_poly()
            self.update_undo_redo_buttons()
            return

        # 常规重做
        if self.redo_stack:
            next_state = self.redo_stack.pop()
            next_signature = self._redo_signatures.pop()
            self.undo_stack.append(next_state)
            self._undo_signatures.append(next_signature)
            self.restore_state(next_state)
            self.update_undo_redo_buttons()
            
    def update_undo_redo_buttons(self):
        """更新撤销和重做按钮的可用状态（含多边形顶点撤销）"""
        from jotmark.graphics.canvas import CanvasMode
        # 撤销可用：有历史状态 或 多边形绘制中有顶点可撤销
        can_undo = len(self.undo_stack) > 1
        if self.scene.mode in (CanvasMode.POLY, CanvasMode.LINE) and len(self.scene.poly_pts) > 0:
            can_undo = True

        # 重做可用：有重做状态 或 多边形绘制中有被撤销的顶点
        can_redo = len(self.redo_stack) > 0
        if self.scene.mode in (CanvasMode.POLY, CanvasMode.LINE) and hasattr(self, '_poly_redo_pts') and self._poly_redo_pts:
            can_redo = True

        self.btnUndo.setEnabled(can_undo)
        self.btnRedo.setEnabled(can_redo)
        
        # 强制刷新图标渲染
        from PySide6.QtGui import QIcon
        self.btnUndo.setIcon(self.set_icon_color(QIcon("ui/icon/arrow-u-up-left.svg"), self.current_icon_color))
        self.btnRedo.setIcon(self.set_icon_color(QIcon("ui/icon/arrow-u-up-right.svg"), self.current_icon_color))

    def restore_state(self, state):
        """根据快照数据，完全重建画板元素"""
        
        # 记录当前选中状态以便恢复
        selected_labels_or_poses = []
        for item in self.scene.selectedItems():
            if isinstance(item, PoseShape):
                # We can't perfectly match pose instances across re-creation easily, 
                # but we can try to match by rect center and label
                selected_labels_or_poses.append({"type": "pose", "center": item.pos()})
            elif hasattr(item, 'label'):
                selected_labels_or_poses.append({"type": "other", "label": item.label})

        self.scene.clear_shapes()
        for shape_data in state:
            label = shape_data.get("label", "")
            shape_type = shape_data.get("type", "")
            points = shape_data.get("points", [])

            # 同步类别列表
            if label and label not in self.class_list:
                # self.class_list.append(label)
                # self.listClasses.addItem(label)
                self.add_class_to_list(label)
                self.save_classes()

            shape = None
            if shape_type == "rectangle" and len(points) == 2:
                rect = QRectF(points[0][0], points[0][1], points[1][0] - points[0][0], points[1][1] - points[0][1])
                shape = RectShape(rect, label)
            elif shape_type == "polygon" and len(points) >= 3:
                qpoints = [QPointF(p[0], p[1]) for p in points]
                shape = PolyShape(QPolygonF(qpoints), label)
            elif shape_type in ("linestrip", "polyline", "line") and len(points) >= 2:
                qpoints = [QPointF(p[0], p[1]) for p in points]
                shape = PolylineShape(QPolygonF(qpoints), label)
            elif shape_type == "point" and len(points) == 1:
                shape = PointShape(QPointF(points[0][0], points[0][1]), label)
            elif shape_type == "obb":
                rect_data = shape_data.get("rect")
                angle = shape_data.get("angle", 0)
                if rect_data and len(rect_data) == 4:
                    cx, cy, w, h = rect_data[0], rect_data[1], rect_data[2], rect_data[3]
                    shape = RotatedRectShape(cx, cy, w, h, angle, label)
            elif shape_type == "pose":
                rect_data = shape_data.get("rect")
                template_name = shape_data.get("template_name", "")
                kps_data = shape_data.get("keypoints", [])
                if rect_data and len(rect_data) == 4:
                    cx, cy, w, h = rect_data
                    angle = shape_data.get("angle", 0)
                    template = self.template_manager.get_template(template_name)
                    if not template:
                        template = {"name": template_name, "keypoints": [], "connections": []}
                        for i, kp in enumerate(kps_data):
                            template["keypoints"].append({"name": f"kp_{i}", "color": "#00FF00", "default_pos": [0.5, 0.5]})
                    
                    shape = PoseShape(QRectF(cx - w / 2, cy - h / 2, w, h), template, label)
                    shape.setRotation(angle)
                    for i, kp_data in enumerate(kps_data):
                        if i < len(shape.kps):
                            local_pt = shape.mapFromScene(QPointF(kp_data[0], kp_data[1]))
                            shape.kps[i].setPos(local_pt)
                            shape.kps[i].set_visibility(kp_data[2])
                    shape.update_lines()

            if shape:
                saved_attributes = shape_data.get("attributes", {})
                shape.attributes = dict(saved_attributes) if isinstance(saved_attributes, dict) else {}
                shape._json_metadata = deepcopy(shape_data.get("_json_metadata", {}))
                self.scene.addItem(shape)
                if hasattr(shape, 'update_label_text'):
                    shape.update_label_text(label)
                if hasattr(shape, 'update_label_position'):
                    shape.update_label_position(shape)
                # 应用类别颜色
                if hasattr(shape, 'set_color'):
                    shape.set_color(self.classListWidget.get_class_color(label))
                
                # 尝试恢复选中状态 (保持编辑模式)
                is_selected = False
                if shape_type == "pose":
                    for s in selected_labels_or_poses:
                        if s["type"] == "pose" and (s["center"] - shape.pos()).manhattanLength() < 5:
                            is_selected = True
                            break
                else:
                    for s in selected_labels_or_poses:
                        if s["type"] == "other" and s["label"] == label:
                            is_selected = True
                            break
                
                shape.setSelected(is_selected)
                
                if hasattr(shape, 'update_label_visibility'):
                    shape.update_label_visibility(shape, is_selected=is_selected, is_hovered=False)
                if hasattr(shape, '_update_handle_visibility'):
                    shape._update_handle_visibility()

        self.auto_save_annotation()
        self.update_annotation_tree()

    def open_dataset_tool(self):
        if self.manual_only or TrainingPlatformWindow is None:
            DialogOver(self, "手工标注版未安装训练平台。", "当前版本", "info")
            return
        try:
            if not hasattr(self, 'dataset_window') or self.dataset_window is None:
                self.dataset_window = TrainingPlatformWindow()
            # 默认最大化显示窗口
            self.dataset_window.showMaximized()
            # 把窗口强制拉到最前面
            self.dataset_window.raise_()
            self.dataset_window.activateWindow()

        except Exception as e:
            DialogOver(self, f"启动失败: {e}", "系统错误", "danger")

    def trigger_sam_prompt(self):
        if self.current_format == "curvelanes":
            DialogOver(
                self,
                "CurveLanes 格式仅保存手工绘制的开放折线；SAM 的矩形、多边形结果不能转换为车道线标注。",
                "该格式不支持智能提取",
                "warning",
            )
            return
        prompt = self.samPromptInput.text().strip()
        if not prompt:
            DialogOver(self, "请输入提示词进行提取！", "提示", "warning")
            return
        if not self._ensure_pending_annotation_saved():
            return

        # 收集所有勾选的文件
        checked_paths = []
        for i in range(self.listFiles.count()):
            item = self.listFiles.item(i)
            if item and item.checkState() == Qt.Checked:
                checked_paths.append(self._file_item_path(item))

        if len(checked_paths) > 1:
            # 批量处理逻辑
            if not self._ensure_pending_annotation_saved():
                return
            if self.sam_client.current_model_type != "sam3" or not self.sam_client.model or not self.sam_client.processor:
                DialogOver(self, "请先在上方选择并加载 SAM 3 模型以进行批量提示词处理！", "提示", "warning")
                return

            import re
            prompts = [p.strip() for p in re.split(r'[,，、\s]+', prompt) if p.strip()]
            if not prompts:
                DialogOver(self, "输入的提示词无效，请重新输入！", "提示", "warning")
                return

            self.batch_dialog = BatchProgressDialog(self, self.is_dark_theme)
            
            self.batch_worker = SamBatchWorker(
                processor=self.sam_client.processor,
                model=self.sam_client.model,
                img_paths=checked_paths,
                prompts=prompts,
                current_format=self.current_format,
                class_list=self.class_list,
                canvas_mode=self.scene.mode,
                overwrite=self.btnOverwrite.isChecked(),
                device=getattr(self.sam_client, "device", None),
                annotation_output_dir=self.annotation_output_dir,
            )

            # 连接进度更新
            def update_progress(current, total, filename):
                percent = int((current / total) * 100) if total > 0 else 0
                self.batch_dialog.progress_bar.setValue(percent)
                self.batch_dialog.status_label.setText(f"正在标注 ({current + 1}/{total}):\n{filename}")
                
            self.batch_worker.progress.connect(update_progress)

            # 完成回调
            def on_finished(processed, total):
                self.batch_dialog.movie.stop()
                self.batch_dialog.accept()
                
                # 同步类别到历史面板
                for p in prompts:
                    if p not in self.class_list:
                        self.add_class_to_list(p)
                self.save_classes()
                
                # 如果当前打开的图片在被批量标注的列表中，重新加载以显示新标注
                if self.current_image_path in checked_paths:
                    if not self._ensure_pending_annotation_saved():
                        return
                    self.scene.clear_shapes()
                    self.load_annotations(self.current_image_path)
                    self.apply_class_colors_to_scene()
                    self.update_annotation_tree()
                    self._remember_current_annotation_signature()
                    self.push_state()
                self.update_annotation_status()
                
                DialogOver(self, f"批量标注完成！成功处理 {processed}/{total} 张图片。", "批量标注", "success")
                
            self.batch_worker.finished.connect(on_finished)

            # 错误回调
            def on_error(err_msg):
                self.batch_dialog.movie.stop()
                self.batch_dialog.reject()
                DialogOver(self, f"批量标注出错: {err_msg}", "错误", "danger")
                
            self.batch_worker.error.connect(on_error)

            # 取消按钮连接
            self.batch_dialog.btn_cancel.clicked.connect(lambda: setattr(self.batch_worker, 'is_cancelled', True))

            self.batch_worker.start()
            self.batch_dialog.exec()
        else:
            # 单张图片处理逻辑
            if self.scene.mode == CanvasMode.POINT:
                DialogOver(self, "点标注模式下无法使用 SAM 智能提取", "提示", "warning")
                return

            if not self.current_image_path:
                DialogOver(self, "请先打开一张图片！", "提示", "warning")
                return

            self.samSwitch.setChecked(True)
            self.helpLabel.setText(f"正在提取提示词: {prompt}...")
            self.helpLabel.setStyleSheet("color: orange;")
            self.sam_client.request_text_inference(prompt)

    def handle_text_results(self, results, prompt_text):
        if not results:
            self.helpLabel.setText(f"提取完成: 未发现关于 '{prompt_text}' 的目标")
            self.helpLabel.setStyleSheet("color: red;")
            if self.btnOverwrite.isChecked():
                if not self._ensure_pending_annotation_saved():
                    return
                self.scene.clear_shapes()
                self.update_annotation_tree()
                self.auto_save_annotation()
                self.push_state()
            return

        self.helpLabel.setText(f"提取完成: 成功抓取 {len(results)} 个 '{prompt_text}' 目标")
        self.helpLabel.setStyleSheet("color: green;")

        if prompt_text not in self.class_list:
            self.add_class_to_list(prompt_text)
            self.save_classes()

        if self.btnOverwrite.isChecked():
            if not self._ensure_pending_annotation_saved():
                return
            self.scene.clear_shapes()

        for res in results:
            if self.scene.mode == CanvasMode.RECT:
                x, y, w, h = res["rect"]
                shape = RectShape(QRectF(x, y, w, h), prompt_text)
            elif self.scene.mode == CanvasMode.RBOX:
                if "obb" in res and len(res["obb"]) == 5:
                    cx, cy, w, h, angle = res["obb"]
                    shape = RotatedRectShape(cx, cy, w, h, angle, prompt_text)
                else:
                    x, y, w, h = res["rect"]
                    cx = x + w / 2.0
                    cy = y + h / 2.0
                    shape = RotatedRectShape(cx, cy, w, h, 0, prompt_text)
            else:
                qpts = [QPointF(p[0], p[1]) for p in res["poly_pts"]]
                shape = PolyShape(QPolygonF(qpts), prompt_text)

            self.scene.addItem(shape)
            if isinstance(shape, RotatedRectShape):
                shape.constrain_to_scene()
            if hasattr(shape, 'update_label_text'):
                shape.update_label_text(prompt_text)
            if hasattr(shape, 'update_label_position'):
                shape.update_label_position(shape)
            if hasattr(shape, 'update_label_visibility'):
                shape.update_label_visibility(shape, is_selected=False, is_hovered=False)
            if hasattr(shape, 'set_color'):
                shape.set_color(self.classListWidget.get_class_color(prompt_text))

        self.update_annotation_tree()
        self.auto_save_annotation()
        self.push_state()

    def update_sam_merge_button(self):
        if not hasattr(self, "btnMergeSam"):
            return
        selected_shapes = [
            item for item in self.scene.selectedItems()
            if isinstance(item, (RectShape, PolyShape, RotatedRectShape))
            and not getattr(item, "is_temp", False)
        ]
        self.btnMergeSam.setEnabled(True)
        self.btnMergeSam.setChecked(self.sam_merge_selection_mode)

        if self.sam_merge_selection_mode:
            self.btnMergeSam.setIcon(self.set_icon_color(QIcon("ui/icon/merge.svg"), QColor("#FACC15")))
            self.btnMergeSam.setStyleSheet(
                "QToolButton { background-color: rgba(250, 204, 21, 0.25); border: 1px solid #FACC15; border-radius: 6px; }"
                "QToolButton:hover { background-color: rgba(250, 204, 21, 0.38); }"
            )
            self.btnMergeSam.setToolTip(
                f"已选择 {len(selected_shapes)} 个区域；再次点击以精确合并。"
                "按 Esc、点击空白处，或在不足两个区域时再次点击按钮可取消"
            )
            self.helpLabel.setText(
                f"区域合并选择中：已选择 {len(selected_shapes)} 个区域。"
                "再次点击已选区域可取消；按 Esc 或点击空白处退出。"
            )
            self.helpLabel.setStyleSheet("color: #D97706;")
        else:
            inactive_color = QColor("#94A3B8") if self.is_dark_theme else QColor("#64748B")
            self.btnMergeSam.setIcon(self.set_icon_color(QIcon("ui/icon/merge.svg"), inactive_color))
            self.btnMergeSam.setStyleSheet(
                "QToolButton { border: none; background: transparent; border-radius: 6px; opacity: 0.65; }"
                "QToolButton:hover { background-color: rgba(128, 128, 128, 0.2); }"
            )
            self.btnMergeSam.setToolTip("精确合并：选择多个同类闭合区域后点击；或先点击此按钮进入多选模式")

    def enforce_sam_merge_selection(self):
        if not self.sam_merge_selection_mode or self._enforcing_sam_merge_selection:
            return
        selected_shapes = [
            item for item in self.scene.selectedItems()
            if isinstance(item, (RectShape, PolyShape, RotatedRectShape))
            and not getattr(item, "is_temp", False)
        ]
        if not selected_shapes:
            self.sam_merge_label = None
            return

        if not self.sam_merge_label:
            self.sam_merge_label = selected_shapes[0].label

        invalid_shapes = [shape for shape in selected_shapes if shape.label != self.sam_merge_label]
        if invalid_shapes:
            self._enforcing_sam_merge_selection = True
            try:
                for shape in invalid_shapes:
                    shape.setSelected(False)
            finally:
                self._enforcing_sam_merge_selection = False
            self.statusBar.showMessage(f"只能选择“{self.sam_merge_label}”类别的标注片段。", 4500)
            self.helpLabel.setText("区域合并选择中：不同类别的标注已被自动排除。")
            self.helpLabel.setStyleSheet("color: #DC2626;")

    @staticmethod
    def _exact_union_polygon(shapes):
        """Return one exact vector union polygon, or ``None`` if impossible.

        ``PolyShape`` stores only one outer boundary.  Multiple disconnected
        islands and holes cannot be represented without inventing or erasing
        labeled area, so such selections are deliberately rejected.
        """
        union_path = QPainterPath()
        union_path.setFillRule(Qt.WindingFill)
        for shape in shapes:
            path = get_shape_scene_path(shape)
            path.setFillRule(Qt.WindingFill)
            union_path = union_path.united(path)

        union_path = union_path.simplified()
        fill_polygons = union_path.toFillPolygons()
        subpaths = union_path.toSubpathPolygons()
        if len(fill_polygons) != 1 or len(subpaths) != 1:
            return None

        polygon = QPolygonF(fill_polygons[0])
        # Qt commonly repeats the first point at the end. PolyShape closes its
        # own contour, so remove only that redundant endpoint.
        if polygon.count() > 1 and polygon.first() == polygon.last():
            polygon.remove(polygon.count() - 1)
        return polygon if polygon.count() >= 3 else None

    def _finish_region_merge_selection(self):
        self.sam_merge_selection_mode = False
        self.scene.merge_selection_mode = False
        self.sam_merge_label = None
        self.classListWidget.set_merge_selection_mode(False)

    def cancel_region_merge_selection(self):
        """Exit temporary merge selection without changing annotation geometry."""
        was_active = self.sam_merge_selection_mode or self.scene.merge_selection_mode
        self._finish_region_merge_selection()
        for item in list(self.scene.selectedItems()):
            item.setSelected(False)
        self.update_annotation_tree()
        self.update_attribute_editor()
        self.update_sam_merge_button()
        if was_active:
            self.helpLabel.setText("已取消区域合并；原有标注未作任何修改。")
            self.helpLabel.setStyleSheet("color: #64748B;")
            self.statusBar.showMessage("已取消区域合并。", 3000)

    def merge_selected_regions(self):
        selected_shapes = [
            item for item in self.scene.selectedItems()
            if isinstance(item, (RectShape, PolyShape, RotatedRectShape))
            and not getattr(item, "is_temp", False)
        ]

        # With fewer than two selected regions, a second toolbar click exits
        # temporary merge mode instead of trapping the user there.
        if self.sam_merge_selection_mode and len(selected_shapes) < 2:
            self.cancel_region_merge_selection()
            return

        # Ctrl-click can already select multiple regions. If fewer than two
        # are selected, enter the dedicated extended-selection mode; the next
        # click of this button confirms the exact union.
        if not self.sam_merge_selection_mode and len(selected_shapes) < 2:
            self.sam_merge_selection_mode = True
            self.scene.merge_selection_mode = True
            self.sam_merge_label = None
            self.classListWidget.set_merge_selection_mode(True)
            self.update_sam_merge_button()
            self.statusBar.showMessage(
                "区域合并：点击区域可选择/取消；按 Esc、点击空白处或再次点击按钮可取消。",
                7000,
            )
            return

        if len(selected_shapes) < 2:
            self.helpLabel.setText("区域合并选择中：至少选择两个同类别闭合区域后，再次点击合并按钮。")
            self.helpLabel.setStyleSheet("color: #D97706;")
            self.statusBar.showMessage("尚未选够两个区域，请继续点击标注。", 4000)
            return

        labels = {getattr(shape, "label", "") for shape in selected_shapes}
        if len(labels) != 1:
            self.helpLabel.setText("区域合并选择中：只能选择同一类别的区域。再次点击错误选择即可取消。")
            self.helpLabel.setStyleSheet("color: #DC2626;")
            self.statusBar.showMessage("所选标注类别不一致，请取消不需要的选择后重试。", 5000)
            return

        polygon = self._exact_union_polygon(selected_shapes)
        if polygon is None:
            self.helpLabel.setText("无法无损合并：区域必须形成一个无孔、连续的外轮廓。")
            self.helpLabel.setStyleSheet("color: #DC2626;")
            self.statusBar.showMessage("所选区域彼此分离、仅点接触或形成孔洞；已保留原标注。", 6000)
            return

        label = labels.pop()
        attributes = getattr(selected_shapes[0], "attributes", {})
        keep_attributes = all(getattr(shape, "attributes", {}) == attributes for shape in selected_shapes)
        merged_shape = PolyShape(polygon, label)
        merged_shape.attributes = dict(attributes) if keep_attributes and isinstance(attributes, dict) else {}

        for shape in selected_shapes:
            if shape.scene() is self.scene:
                self.scene.removeItem(shape)
        self.scene.addItem(merged_shape)
        merged_shape.set_color(self.classListWidget.get_class_color(label))
        merged_shape.setSelected(True)

        self._finish_region_merge_selection()
        self.update_annotation_tree()
        self.update_attribute_editor()
        self.auto_save_annotation()
        self.push_state()
        self.helpLabel.setText(f"已精确合并 {len(selected_shapes)} 个区域；未调用 SAM3。")
        self.helpLabel.setStyleSheet("color: green;")
        self.statusBar.showMessage("区域已按选中轮廓的几何并集完成合并。", 5000)
        self.update_sam_merge_button()

    # Backward-compatible API for extensions that still use the old method.
    # The operation is intentionally now a local, exact geometry union.
    def merge_selected_with_sam3(self):
        self.merge_selected_regions()

    def delete_selected(self):
        for item in self.scene.selectedItems():
            self.scene.removeItem(item)
        self.scene.state_changed.emit()

    def toggle_sidebar(self):
        if self.toolBar.toolButtonStyle() == Qt.ToolButtonTextBesideIcon:
            # 隐藏文字 — 收缩模式
            self.toolBar.setToolButtonStyle(Qt.ToolButtonIconOnly)
            self.toolBar.setFixedWidth(50)
            self.logoLabel.hide()
            
            # 收缩模式：缩小 Logo 图标
            logo_path = "ui/icon/logo.png"
            pix = QPixmap(logo_path).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.logoIcon.setPixmap(pix)
            self.logoIcon.setFixedSize(24, 24)

            # 释放按钮宽度限制
            for btn in self._actionButtons:
                btn.setMinimumWidth(0)

            self.formatWidget.set_icon_only(True)
            self.btnDatasetTool.setText("")
            self.samIcon.hide() # 收缩时隐藏左侧图标
            # SAM 开关竖向显示
            self.samSwitch.setFixedSize(26, 50)
            self.samSwitch._vertical = True
            self.samSwitch.update()
        else:
            # 显示文字 — 展开模式
            self.toolBar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.toolBar.setFixedWidth(190)
            
            # 展开模式：恢复 Logo 图标和文字
            self.logoLabel.show()
            self.logoLabel.setText("JotMark")
            logo_path = "ui/icon/logo.png"
            pix = QPixmap(logo_path).scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.logoIcon.setPixmap(pix)
            self.logoIcon.setFixedSize(28, 28)

            # 恢复按钮宽度
            for btn in self._actionButtons:
                btn.setMinimumWidth(180)

            self.formatWidget.set_icon_only(False)
            self.btnDatasetTool.setText("一键训练平台")
            self.samIcon.show() # 展开时显示左侧图标
            # SAM 开关横向显示
            self.samSwitch.setFixedSize(50, 26)
            self.samSwitch._vertical = False
            self.samSwitch.update()

    def set_icon_color(self, icon, color):
        from PySide6.QtGui import QPainter, QIcon, QPixmap, QColor
        pixmap = icon.pixmap(100, 100)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color)
        painter.end()
        
        # 核心逻辑：自己创建一个可以响应 enable/disable 的 QIcon
        # 我们把这个纯色图作为 Normal 状态
        new_icon = QIcon()
        new_icon.addPixmap(pixmap, QIcon.Normal, QIcon.On)
        new_icon.addPixmap(pixmap, QIcon.Normal, QIcon.Off)
        
        # 为了让禁用状态变灰，我们用 QPainter 绘制一个半透明的版本作为 Disabled 状态
        disabled_pixmap = icon.pixmap(100, 100)
        dpainter = QPainter(disabled_pixmap)
        dpainter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        
        # 深色主题下禁用颜色为半透明白，浅色主题下为半透明黑
        if color.lightness() > 128:
            disabled_color = QColor(255, 255, 255, 60)
        else:
            disabled_color = QColor(15, 23, 42, 60)
            
        dpainter.fillRect(disabled_pixmap.rect(), disabled_color)
        dpainter.end()
        
        new_icon.addPixmap(disabled_pixmap, QIcon.Disabled, QIcon.On)
        new_icon.addPixmap(disabled_pixmap, QIcon.Disabled, QIcon.Off)
        
        return new_icon

    def toggle_theme(self):
        self.is_dark_theme = not self.is_dark_theme
        QSettings("luohuabuxiema", "JotMark").setValue("dark_theme", self.is_dark_theme)
        if self.is_dark_theme:
            self.setStyleSheet(DARK_THEME)
            self.btnThemeToggle.setText("☀")
            
            # 更新深色图标
            from PySide6.QtGui import QIcon, QColor
            self.current_icon_color = QColor(255, 255, 255)
            self.btnUndo.setIcon(self.set_icon_color(QIcon("ui/icon/arrow-u-up-left.svg"), self.current_icon_color))
            self.btnRedo.setIcon(self.set_icon_color(QIcon("ui/icon/arrow-u-up-right.svg"), self.current_icon_color))
            self.btnDelete.setIcon(self.set_icon_color(QIcon("ui/icon/trash.svg"), self.current_icon_color))
            self.btnDeleteFiles.setIcon(self.set_icon_color(QIcon("ui/icon/trash.svg"), self.current_icon_color))
            self.btnOverwrite.setIcon(self.set_icon_color(QIcon("ui/icon/编辑.svg"), self.current_icon_color))
            self.btnFileExtensions.setIcon(self.set_icon_color(QIcon("ui/icon/格式.svg"), self.current_icon_color))
            self.btnFileSort.setIcon(self.set_icon_color(QIcon("ui/icon/sort.svg"), self.current_icon_color))
            self.btnSave.setIcon(self.set_icon_color(QIcon("ui/icon/floppy-disk.svg"), self.current_icon_color))
            self.btnAnnotationOutputDir.setIcon(self.set_icon_color(QIcon("ui/icon/folder.svg"), self.current_icon_color))
            self.btnMergeSam.setIcon(self.set_icon_color(QIcon("ui/icon/merge.svg"), self.current_icon_color))
            self.btnKeyboard.setIcon(self.set_icon_color(QIcon("ui/icon/keyboard.svg"), self.current_icon_color))
            
            # 更新侧边栏图标
            self.actionOpen.setIcon(self.set_icon_color(QIcon("ui/icon/folder.svg"), self.current_icon_color))
            self.actionSelect.setIcon(self.set_icon_color(QIcon("ui/icon/select.svg"), self.current_icon_color))
            self.actionRect.setIcon(self.set_icon_color(QIcon("ui/icon/rectangle.svg"), self.current_icon_color))
            self.actionPoly.setIcon(self.set_icon_color(QIcon("ui/icon/polygon.svg"), self.current_icon_color))
            self.actionLine.setIcon(self.set_icon_color(QIcon("ui/icon/polyline.svg"), self.current_icon_color))
            self.actionPoint.setIcon(self.set_icon_color(QIcon("ui/icon/关键点.svg"), self.current_icon_color))
            self.actionRBox.setIcon(self.set_icon_color(QIcon("ui/icon/手机旋转1.svg"), self.current_icon_color))
            self.samIcon.setPixmap(self.set_icon_color(QIcon("ui/icon/魔法-copy.svg"), self.current_icon_color).pixmap(24, 24))
            self.btnDatasetTool.setIcon(self.set_icon_color(QIcon("ui/icon/wrench.svg"), self.current_icon_color))
            self.formatWidget.btn.setIcon(self.set_icon_color(QIcon("ui/icon/格式.svg"), self.current_icon_color))
        else:
            self.setStyleSheet(LIGHT_THEME)
            self.btnThemeToggle.setText("🌙")
            
            # 更新浅色图标
            from PySide6.QtGui import QIcon, QColor
            self.current_icon_color = QColor(15, 23, 42) # 深灰色
            self.btnUndo.setIcon(self.set_icon_color(QIcon("ui/icon/arrow-u-up-left.svg"), self.current_icon_color))
            self.btnRedo.setIcon(self.set_icon_color(QIcon("ui/icon/arrow-u-up-right.svg"), self.current_icon_color))
            self.btnDelete.setIcon(self.set_icon_color(QIcon("ui/icon/trash.svg"), self.current_icon_color))
            self.btnDeleteFiles.setIcon(self.set_icon_color(QIcon("ui/icon/trash.svg"), self.current_icon_color))
            self.btnOverwrite.setIcon(self.set_icon_color(QIcon("ui/icon/编辑.svg"), self.current_icon_color))
            self.btnFileExtensions.setIcon(self.set_icon_color(QIcon("ui/icon/格式.svg"), self.current_icon_color))
            self.btnFileSort.setIcon(self.set_icon_color(QIcon("ui/icon/sort.svg"), self.current_icon_color))
            self.btnSave.setIcon(self.set_icon_color(QIcon("ui/icon/floppy-disk.svg"), self.current_icon_color))
            self.btnAnnotationOutputDir.setIcon(self.set_icon_color(QIcon("ui/icon/folder.svg"), self.current_icon_color))
            self.btnMergeSam.setIcon(self.set_icon_color(QIcon("ui/icon/merge.svg"), self.current_icon_color))
            self.btnKeyboard.setIcon(self.set_icon_color(QIcon("ui/icon/keyboard.svg"), self.current_icon_color))
            
            # 更新侧边栏图标
            self.actionOpen.setIcon(self.set_icon_color(QIcon("ui/icon/folder.svg"), self.current_icon_color))
            self.actionSelect.setIcon(self.set_icon_color(QIcon("ui/icon/select.svg"), self.current_icon_color))
            self.actionRect.setIcon(self.set_icon_color(QIcon("ui/icon/rectangle.svg"), self.current_icon_color))
            self.actionPoly.setIcon(self.set_icon_color(QIcon("ui/icon/polygon.svg"), self.current_icon_color))
            self.actionLine.setIcon(self.set_icon_color(QIcon("ui/icon/polyline.svg"), self.current_icon_color))
            self.actionPoint.setIcon(self.set_icon_color(QIcon("ui/icon/关键点.svg"), self.current_icon_color))
            self.actionRBox.setIcon(self.set_icon_color(QIcon("ui/icon/手机旋转1.svg"), self.current_icon_color))
            self.samIcon.setPixmap(self.set_icon_color(QIcon("ui/icon/魔法-copy.svg"), self.current_icon_color).pixmap(24, 24))
            self.btnDatasetTool.setIcon(self.set_icon_color(QIcon("ui/icon/wrench.svg"), self.current_icon_color))
            self.formatWidget.btn.setIcon(self.set_icon_color(QIcon("ui/icon/格式.svg"), self.current_icon_color))
        self.classListWidget.set_theme(self.is_dark_theme)
        self.update_prompt_btn_icon()
        self.update_sam_merge_button()

    def show_help_dialog(self):
        help_text = """
        <h3>【快捷键大全】</h3>
        <ul>
            <li><b>A / 左方向键 / Page Up</b>：上一张图片</li>
            <li><b>D / 右方向键 / Page Down</b>：下一张图片</li>
            <li><b style="color:red;">Ctrl + S</b>：保存当前标注</li>
            <li><b style="color:blue;">Q</b>：开启/关闭 SAM 智能辅助</li>
            <li><b>R</b>：切换至 矩形标注</li>
            <li><b>V</b>：切换至选择/编辑模式；右侧可直接点击任一具体标注同步选中画布。右击该标注可修改类别或删除；也可将其拖到另一类别下改类。</li>
            <li><b>P</b>：切换至 多边形标注</li>
            <li><b>L</b>：切换至 折线标注</li>
            <li><b>T</b>：切换至 关键点标注</li>
            <li><b>O</b>：切换至 旋转框标注</li>
            <li><b>M</b>：触发 模型预测 (Smart 模式并加载模型时可用)</li>
            <li><b>精确区域合并</b>：选中两个及以上同类、相连的闭合区域，点击保存按钮右侧的合并图标；程序只计算几何并集，不调用 SAM3 重绘区域</li>
            <li><b>Del / Backspace</b>：删除当前选中的标注框</li>
            <li><b>F1</b>：打开此帮助文档</li>
        </ul>
        <hr>
        <h3>【多边形绘制技巧】</h3>
        <ul>
            <li><b>左键点击</b>：添加顶点</li>
            <li><b>Ctrl + Z</b>：撤销上一个顶点</li>
            <li><b>双击 / Enter</b>：闭合多边形</li>

        </ul>
        <hr>
        <h3>【旋转框绘制快捷键】</h3>
        <ul>
            <li><b> Z / V</b>：每次向左/向右旋转 5°</li>
            <li><b>X / C</b>：每次向左/向右旋转 1°</li>
        </ul>
        <hr>
        <h3>【SAM 智能辅助】</h3>
        <ul>
            <li><b>鼠标点选</b>：开启开关后，鼠标悬停预览，点击直接确认生成高精度轮廓。</li>
            <li><b>提示词提取</b>：在右下角输入框输入目标名称（如: dog、cat），按回车即可一键全图抓取并打好框！左侧选中的是“矩形”还是“多边形”格式。</li>
        </ul>
        """
        QMessageBox.about(self, "JotMark 使用说明", help_text)

    def update_coordinate_label(self, x, y):
        self.coordLabel.setText(f"坐标: X: {x}, Y: {y}")

    def on_sam_toggled(self, checked):
        if self.manual_only:
            if checked:
                self.samSwitch.setChecked(False)
            return
        if checked and self.scene.mode == CanvasMode.LINE:
            self.samSwitch.setChecked(False)
            self.statusBar.showMessage("折线标注不支持 SAM 智能提取，已切换为手动绘制", 4000)
            return
        sam_ready = self._sam_model_is_ready()
        yolo_ready = getattr(self, "current_yolo_predictor", None) is not None
        if checked and not (sam_ready or yolo_ready):
            # Smart mode is opt-in, but choosing it must never start a default
            # model or leave the canvas in a non-functional inference state.
            self.scene.set_sam_enabled(False)
            self.samSwitch.blockSignals(True)
            self.samSwitch.setChecked(False)
            self.samSwitch.blockSignals(False)
            self.btnDrawMode.setChecked(True)
            self.btnModelSelector.show()
            self.helpLabel.setText("尚未加载辅助模型；请点击“加载辅助模型”，或继续手工标注。")
            self.helpLabel.setStyleSheet("color: orange;")
            self.statusBar.showMessage("辅助模型未加载，当前保持手工标注模式。", 5000)
            return

        # Only SAM models change the canvas into click-prompt inference mode.
        # A loaded YOLO model uses its explicit prediction button instead.
        self.scene.set_sam_enabled(checked and sam_ready)
        self._update_help_text(self.scene.mode)
        
        if checked:
            self.btnModelSelector.show()
            if yolo_ready:
                self.btnPredict.show()
                self.btnClassFilter.show()
            if not self.btnSmartMode.isChecked():
                self.btnSmartMode.setChecked(True)
        else:
            # The model button stays available in manual mode so users can
            # explicitly load a model later without first enabling Smart.
            self.btnModelSelector.show()
            self.btnPredict.hide()
            self.btnClassFilter.hide()
            if not self.btnDrawMode.isChecked():
                self.btnDrawMode.setChecked(True)

        if self.scene.mode == CanvasMode.POINT:
            if checked and (sam_ready or yolo_ready):
                self.templateWidget.hide()
                self.sepTemplate.hide()
            else:
                self.templateWidget.show()
                self.sepTemplate.show()

    def on_pose_template_changed(self, text):
        if not text: return
        if text == "+ New Template...":
            dlg = SkeletonTemplateDialog(self, self.template_manager, self.is_dark_theme)
            if dlg.exec() == QDialog.Accepted:
                self._init_pose_templates()
                # Select the newly created template
                last_template = self.template_manager.get_template_names()[-1]
                self.templateWidget._on_template_selected(last_template, f"{last_template} ▾")
            else:
                self.templateWidget._on_template_selected("Person (COCO)", "Person (COCO) ▾")
        else:
            self.scene.current_pose_template = self.template_manager.get_template(text)
            self._update_help_text(self.scene.mode)
            self.formatWidget.set_yolo_enabled(True)
        # Ensure we are in point mode
        self._set_mode(CanvasMode.POINT)

    def _set_mode(self, mode):
        if self.manual_only and self.samSwitch.isChecked():
            self.samSwitch.setChecked(False)
        if mode in (CanvasMode.LINE, CanvasMode.EDIT) and self.samSwitch.isChecked():
            self.samSwitch.setChecked(False)
        self.scene.set_mode(mode)
        # 右侧标注树只在箭头选择/编辑模式下允许拖放改类，避免绘制模式误操作。
        self.classListWidget.set_edit_mode(mode == CanvasMode.EDIT)
        # 切换模式时清空多边形顶点重做栈
        self._poly_redo_pts = []
        mode_name = CanvasMode.get_mode_name(mode)
        self.modeLabel.setText(f"模式: {mode_name}标注")
        self._update_help_text(mode)

        if mode == CanvasMode.EDIT:
            self.actionSelect.setChecked(True)
        elif mode == CanvasMode.RECT:
            self.actionRect.setChecked(True)
        elif mode == CanvasMode.POLY:
            self.actionPoly.setChecked(True)
        elif mode == CanvasMode.LINE:
            self.actionLine.setChecked(True)
        elif mode == CanvasMode.POINT:
            self.actionPoint.setChecked(True)
        elif mode == CanvasMode.RBOX:
            self.actionRBox.setChecked(True)

        if mode == CanvasMode.POINT:
            yolo_pred = getattr(self, 'current_yolo_predictor', None)
            is_yolo_pose = yolo_pred is not None and getattr(yolo_pred, 'task', '') == 'pose'

            if self.samSwitch.isChecked():
                self.templateWidget.hide()
                self.sepTemplate.hide()
            else:
                self.templateWidget.show()
                self.sepTemplate.show()

            self.samSwitch.setEnabled(True)
            
            # 移除自动开启逻辑，保持用户当前的手动/智能选择
            
            self.samPromptInput.setEnabled(False)
            self.samPromptBtn.setEnabled(False)
            self.samPromptInput.setPlaceholderText("点标注模式下文本提示不可用")
            
            if not self.scene.current_pose_template and not is_yolo_pose:
                self.formatWidget.set_yolo_enabled(False)
            else:
                self.formatWidget.set_yolo_enabled(True)
        else:
            self.templateWidget.hide()
            self.sepTemplate.hide()
            is_line_mode = mode == CanvasMode.LINE
            is_edit_mode = mode == CanvasMode.EDIT
            self.samSwitch.setEnabled(not is_line_mode and not is_edit_mode)
            
            supports_text = self.sam_client.supports_text_prompt()
            self.samPromptInput.setEnabled(supports_text and not is_line_mode and not is_edit_mode)
            self.update_prompt_btn_state()
            
            if is_edit_mode:
                self.samPromptInput.setPlaceholderText("选择已有标注后，可在右侧类别列表中调整分类")
            elif supports_text:
                self.samPromptInput.setPlaceholderText("输入提示词提取 (如: dog、cat)")
            else:
                self.samPromptInput.setPlaceholderText("当前模型不支持提示词")
                
            self.formatWidget.set_yolo_enabled(not is_line_mode)

    def _update_help_text(self, mode):
        # 只有在智能模式开启，并且当前选择的是SAM模型时，才显示悬停预览提示
        current_model_key = getattr(self.sam_client, 'current_model_key', None) or ''
        is_sam = self.samSwitch.isChecked() and current_model_key.startswith('sam')
        if mode == CanvasMode.EDIT:
            self.helpLabel.setText("操作: 仅选择和编辑已有标注；点击类别英文切换标签，或将右侧标注拖到其他类别下")
        elif mode == CanvasMode.RECT:
            if is_sam:
                self.helpLabel.setText("操作: 鼠标悬停实时预览外接矩形，左键点击直接确认生成矩形框")
            else:
                self.helpLabel.setText("操作: 拖动鼠标绘制常规矩形")
        elif mode == CanvasMode.POLY:
            if is_sam:
                self.helpLabel.setText("操作: 鼠标悬停实时预览轮廓节点，左键点击直接确认生成多边形")
            else:
                self.helpLabel.setText("操作: 点击添加顶点，双击闭合多边形")
        elif mode == CanvasMode.LINE:
            self.helpLabel.setText("操作: 点击添加折线节点，双击 / Enter / 右键结束；选中后可拖动或在线段中间插入节点")
        elif mode == CanvasMode.POINT:
            self.helpLabel.setText("操作: 点击放置骨架模板，选定后可通过手柄拖拽放大、旋转或微调关键点")
        elif mode == CanvasMode.RBOX:
            self.helpLabel.setText("操作: 拖动绘制旋转框，Z/X/C/V调整角度")

    def load_classes(self, dir_path):
        self.class_list.clear()
        self.classListWidget.load_classes(dir_path)
        self.class_list = self.classListWidget.get_class_list()
        self.attribute_store.load(dir_path)
        if self.attribute_store.load_error:
            self.set_runtime_error("属性配置读取失败，已禁止覆盖", self.attribute_store.load_error)
        if not self.class_list:
            default_classes = self._read_class_names(DEFAULT_CLASS_TEMPLATE_PATH)
            if default_classes:
                self._apply_class_names(default_classes, replace=False)
                self.statusBar.showMessage(
                    "新项目已加载默认类别：person、car、cyclist；可点击右侧“导入”替换或追加。",
                    7000,
                )

    @staticmethod
    def _read_class_names(file_path):
        """Read a UTF-8 one-category-per-line template; # starts a comment."""
        try:
            with open(file_path, "r", encoding="utf-8-sig") as stream:
                names = []
                for line in stream:
                    name = line.strip()
                    if name and not name.startswith("#") and name not in names:
                        names.append(name)
                return names
        except (OSError, UnicodeError):
            return []

    def _apply_class_names(self, class_names, replace=False):
        """Append or replace category definitions without changing annotations."""
        clean_names = []
        for class_name in class_names:
            class_name = str(class_name).strip()
            if class_name and class_name not in clean_names:
                clean_names.append(class_name)
        if not clean_names:
            return []

        if replace:
            previous_attributes = deepcopy(self.attribute_store._classes)
            try:
                for old_name in list(self.class_list):
                    self.attribute_store.remove_class(old_name)
                self.attribute_store.save()
            except Exception:
                self.attribute_store._classes = previous_attributes
                raise
            self.class_list.clear()
            self.classListWidget.clear_classes()

        added = []
        for class_name in clean_names:
            if class_name not in self.class_list:
                self.add_class_to_list(class_name)
                added.append(class_name)
        self.save_classes()
        self.update_annotation_tree()
        if added:
            item = self.classListWidget._class_items.get(added[0])
            if item:
                self.classListWidget.treeWidget.setCurrentItem(item)
        return added

    def import_class_file(self):
        """Import a user-maintained TXT class template."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入类别文件",
            self.current_dir or os.path.dirname(DEFAULT_CLASS_TEMPLATE_PATH),
            "文本文件 (*.txt);;所有文件 (*)",
        )
        if not file_path:
            return
        class_names = self._read_class_names(file_path)
        if not class_names:
            DialogOver(self, "未读取到有效类别。类别文件应每行一个名称；以 # 开头的行会忽略。", "导入失败", "warning")
            return

        shape_types = (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape)
        has_annotations = any(
            isinstance(item, shape_types) and not getattr(item, "is_temp", False)
            for item in self.scene.items()
        )
        annotation_dir = self.annotation_output_dir or self.current_dir
        if annotation_dir:
            has_annotations = has_annotations or any(
                os.path.isfile(os.path.join(annotation_dir, stem + suffix))
                for stem in self._dataset_image_stems()
                for suffix in (".json", ".xml", ".txt", ".lines.txt")
            )
        dialog = QMessageBox(self)
        dialog.setWindowTitle("导入类别文件")
        dialog.setIcon(QMessageBox.Question)
        if has_annotations:
            dialog.setText("当前图片已有标注。为避免类别编号与既有标注错位，只能追加新类别。")
            append_button = dialog.addButton("追加类别", QMessageBox.AcceptRole)
            dialog.addButton("取消", QMessageBox.RejectRole)
            dialog.exec()
            if dialog.clickedButton() != append_button:
                return
            replace = False
        else:
            dialog.setText("请选择导入方式。替换会移除当前类别及其专属属性定义。")
            replace_button = dialog.addButton("替换当前类别", QMessageBox.DestructiveRole)
            append_button = dialog.addButton("追加类别", QMessageBox.AcceptRole)
            dialog.addButton("取消", QMessageBox.RejectRole)
            dialog.exec()
            if dialog.clickedButton() == replace_button:
                replace = True
            elif dialog.clickedButton() == append_button:
                replace = False
            else:
                return

        if replace:
            if not self._can_modify_attribute_schema() or not self._ensure_pending_annotation_saved():
                return
            try:
                self._last_category_backup = self._backup_category_operation()
            except Exception as exc:
                QMessageBox.warning(self, "导入已取消", f"自动备份失败，未替换类别：\n{exc}")
                return
        try:
            added = self._apply_class_names(class_names, replace=replace)
        except Exception as exc:
            QMessageBox.warning(
                self, "类别导入未完成",
                f"导入过程中保存失败，请检查项目文件及自动备份：\n{getattr(self, '_last_category_backup', '')}\n\n{exc}",
            )
            return
        action = "替换" if replace else "追加"
        DialogOver(self, f"已{action}类别文件：新增 {len(added)} 个类别。", "导入成功", "success")

    def import_attribute_file(self):
        """Import a schema file without removing automatic project loading.

        Definitions are replaced only after validation and confirmation.  The
        current schema is backed up first; existing per-shape JSON values are
        intentionally retained rather than silently pruned.
        """
        if not self.current_dir:
            DialogOver(self, "请先通过“打开目录”选择图片项目，再导入属性文件。", "未打开项目", "warning")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入属性配置文件",
            self.current_dir,
            "JotMark 属性文件 (class_attributes.json *.json);;JSON 文件 (*.json);;所有文件 (*)",
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8-sig") as stream:
                raw_schema = json.load(stream, parse_constant=_reject_json_constant)
            # Validate through an isolated store so a malformed file never
            # changes the live project state.
            preview_store = ClassAttributeStore()
            preview = preview_store.replace_schema(raw_schema)
        except (OSError, ValueError, TypeError) as exc:
            DialogOver(self, f"属性文件无效，未作任何修改：\n{exc}", "导入失败", "warning")
            return

        target_path = os.path.join(self.current_dir, ATTRIBUTE_SCHEMA_FILE)
        backup_dir = ""
        if os.path.isfile(target_path):
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_dir = os.path.join(
                self.current_dir,
                ".jotmark_backups",
                f"attribute-schema-import-{timestamp}",
            )
            dialog = QMessageBox(self)
            dialog.setWindowTitle("确认导入属性")
            dialog.setIcon(QMessageBox.Warning)
            dialog.setText("当前项目已有属性配置。导入会替换属性定义。")
            dialog.setInformativeText(
                f"导入文件：\n{file_path}\n\n"
                f"将导入 {preview['global_count']} 个公共属性、"
                f"{preview['class_count']} 个类别的专属属性。\n\n"
                f"现有配置将自动备份到：\n{backup_dir}\n\n"
                "已存在于标注 JSON 中的属性值不会自动删除；若新配置没有对应属性，"
                "这些旧值会保留在 JSON 内但不会显示在表单中。"
            )
            proceed = dialog.addButton("备份并替换", QMessageBox.AcceptRole)
            dialog.addButton("取消", QMessageBox.RejectRole)
            dialog.exec()
            if dialog.clickedButton() != proceed:
                return
            try:
                os.makedirs(backup_dir, exist_ok=False)
                shutil.copy2(target_path, os.path.join(backup_dir, ATTRIBUTE_SCHEMA_FILE))
            except OSError as exc:
                DialogOver(self, f"无法创建属性配置备份，导入已取消：\n{exc}", "导入失败", "warning")
                return

        previous_schema_state = (deepcopy(self.attribute_store._global), deepcopy(self.attribute_store._classes),
                                 deepcopy(self.attribute_store._document_metadata), self.attribute_store.load_error)
        try:
            self.attribute_store.replace_schema(raw_schema)
            self.attribute_store.save()
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            (self.attribute_store._global, self.attribute_store._classes,
             self.attribute_store._document_metadata, self.attribute_store.load_error) = previous_schema_state
            DialogOver(self, f"属性配置写入失败：\n{exc}", "导入失败", "warning")
            return

        self.update_annotation_tree()
        self.update_attribute_editor()
        backup_message = f"\n旧配置备份：{backup_dir}" if backup_dir else ""
        DialogOver(
            self,
            f"已导入 {preview['global_count']} 个公共属性、{preview['class_count']} 个类别属性定义。"
            f"\n配置已保存为：{target_path}{backup_message}",
            "属性导入成功",
            "success",
        )

    def open_category_conversion(self, selected_class=""):
        """Open the copy-on-write class/attribute migration tool.

        The conversion dialog intentionally operates on a selected directory
        instead of the live canvas.  It makes a complete source snapshot and
        writes a separate project, so no open annotation is silently changed.
        """
        source_dir = self.current_dir or self.annotation_output_dir or ""
        dialog = CategoryConversionDialog(source_dir, selected_class, self)
        dialog.exec()

    def save_classes(self):
        if self.current_dir:
            self.classListWidget.set_working_dir(self.current_dir)
            self.classListWidget._class_list = list(self.class_list)
            self.classListWidget.save_classes()

    def apply_class_colors_to_scene(self):
        """加载标注后，根据类别颜色映射为所有形状设置颜色"""
        for item in self.scene.items():
            if isinstance(item, (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape)):
                label = getattr(item, 'label', '')
                if label and hasattr(item, 'set_color'):
                    color = self.classListWidget.get_class_color(label)
                    item.set_color(color)

    def handle_new_shape(self, shape):
        self.scene.addItem(shape)
        if isinstance(shape, RotatedRectShape):
            shape.constrain_to_scene()
        QApplication.processEvents()

        from jotmark.graphics.shapes import PoseShape
        if isinstance(shape, PoseShape) and shape.template:
            # 骨架模板自动使用模板标签作为类别，跳过弹窗
            cls_name = shape.template.get("label", shape.template.get("name", "Unknown"))
            ok = True
        else:
            # An explicit tree choice wins; cleared selection keeps last used class.
            selected_cls = self.classListWidget.get_annotation_class()
            if not selected_cls:
                classes = self.classListWidget.get_class_list()
                selected_cls = classes[0] if classes else "dog"
            cls_name = selected_cls
            ok = True

        if ok and cls_name:
            cls_name = cls_name.strip()
            if cls_name not in self.class_list:
                # self.class_list.append(cls_name)
                # self.listClasses.addItem(cls_name)
                self.add_class_to_list(cls_name)
                self.save_classes()

            shape.label = cls_name
            self.classListWidget.preferred_class = cls_name
            if hasattr(shape, 'update_label_text'):
                shape.update_label_text(cls_name)
            if hasattr(shape, 'update_label_position'):
                shape.update_label_position(shape)
            
            if hasattr(shape, 'update_label_visibility'):
                shape.update_label_visibility(shape, is_selected=True, is_hovered=False)
            # 应用类别颜色
            if hasattr(shape, 'set_color'):
                shape.set_color(self.classListWidget.get_class_color(cls_name))
                
            for item in self.scene.selectedItems():
                item.setSelected(False)

            # A newly created annotation is the one the user is working on:
            # keep it selected so polygon/polyline vertices are visible and
            # the right-side instance-attribute editor has a concrete shape.
            shape.setSelected(True)
            self.update_attribute_editor()
                
            self.push_state()
            self.update_annotation_tree()
            self.auto_save_annotation()
        else:
            self.scene.removeItem(shape)

    def edit_shape_label(self, shape):
        """Reclassify a selected annotation without changing its geometry."""
        if not hasattr(shape, "label"):
            return
        choices = list(self.class_list)
        if shape.label and shape.label not in choices:
            choices.insert(0, shape.label)
        if not choices:
            return
        current_index = choices.index(shape.label) if shape.label in choices else 0
        new_label, accepted = QInputDialog.getItem(self, "调整标注类别", "类别：", choices, current_index, False)
        if accepted and new_label and new_label != shape.label:
            self.on_shape_class_reassigned(shape, new_label)

    def on_shape_label_clicked(self, shape):
        if self.scene.mode == CanvasMode.EDIT:
            self.edit_shape_label(shape)

    def on_canvas_attribute_clicked(self, shape, scope, node_id):
        if self.scene.mode != CanvasMode.EDIT or shape.scene() is not self.scene:
            return
        self.update_attribute_editor()
        if self.attributeEditor._shape is shape:
            self.attributeEditor.edit_attribute_value(scope, node_id)

    def on_canvas_attributes_summary_clicked(self, shape):
        if self.scene.mode != CanvasMode.EDIT or shape.scene() is not self.scene:
            return
        self.update_attribute_editor()
        if self.attributeEditor._shape is shape:
            self.attributeEditor.open_all_attribute_scopes()

    def on_list_item_changed(self, item):
        """ClassListWidget item_changed fallback (rename already handled by class_renamed signal)"""
        pass

    def on_shape_class_reassigned(self, shape, new_class_name):
        """当用户在右侧历史类别列表点击父类别时，将画布上已选中图形的类别修改为该类别"""
        new_class_name = (new_class_name or "").strip()
        if not new_class_name:
            return
        old_label = getattr(shape, 'label', '')
        if old_label == new_class_name:
            return  # 类别未变化，无需操作

        if new_class_name not in self.class_list:
            self.add_class_to_list(new_class_name)
            self.save_classes()

        shape.label = new_class_name
        # Attribute values are class-scoped.  Carrying values from the old
        # class into the new class would create invalid, hidden metadata.
        shape.attributes = {}
        if hasattr(shape, 'update_label_text'):
            shape.update_label_text(new_class_name)
        if hasattr(shape, 'update_label_position'):
            shape.update_label_position(shape)
        # 应用新类别的颜色
        if hasattr(shape, 'set_color'):
            shape.set_color(self.classListWidget.get_class_color(new_class_name))

        # 更新标注树和保存
        self.update_annotation_tree()
        self.update_attribute_editor()
        self.scene.state_changed.emit()

    def manage_class_attributes(self, class_name):
        """Open the schema editor for the selected class."""
        if not self._can_modify_attribute_schema():
            return
        if not class_name:
            DialogOver(self, "请先在右侧类别列表中选中一个类别。", "未选择类别", "warning")
            return
        dialog = AttributeSchemaDialog(class_name, self.attribute_store.attributes_for(class_name), self)
        if dialog.exec() == QDialog.Accepted:
            self._apply_attribute_schema_edit("class", class_name, dialog.get_attributes())

    def manage_global_attributes(self):
        """Edit attributes shown for annotations of every class."""
        if not self._can_modify_attribute_schema():
            return
        dialog = AttributeSchemaDialog("公共属性（全部类别）", self.attribute_store.global_attributes(), self)
        if dialog.exec() == QDialog.Accepted:
            self._apply_attribute_schema_edit("global", "", dialog.get_attributes())

    def _apply_attribute_schema_edit(self, scope, class_name, new_nodes):
        old_nodes = (self.attribute_store.global_attributes() if scope == "global"
                     else self.attribute_store.attributes_for(class_name))
        if new_nodes == old_nodes:
            return True
        if not self._ensure_pending_annotation_saved():
            return False
        try:
            self._last_category_backup = self._backup_category_operation()
            if scope == "global":
                self.attribute_store.set_global_attributes(new_nodes)
                saved_nodes = self.attribute_store.global_attributes()
            else:
                self.attribute_store.set_attributes(class_name, new_nodes)
                saved_nodes = self.attribute_store.attributes_for(class_name)
            self.attribute_store.save()
        except Exception as exc:
            if scope == "global":
                self.attribute_store.set_global_attributes(old_nodes)
            else:
                self.attribute_store.set_attributes(class_name, old_nodes)
            QMessageBox.warning(self, "属性修改失败", f"未完成属性修改，原配置已保留：\n{exc}")
            return False
        removed = ClassAttributeStore._collect_ids(old_nodes) - ClassAttributeStore._collect_ids(saved_nodes)
        self._prune_removed_attribute_values(class_name if scope == "class" else None, removed)
        self.update_annotation_tree()
        self.update_attribute_editor()
        return True

    def _selected_annotation_shapes(self):
        shape_types = (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape)
        return [
            item for item in self.scene.selectedItems()
            if isinstance(item, shape_types) and not getattr(item, "is_temp", False)
        ]

    def update_attribute_editor(self):
        """Show values only for one annotation; every shape owns its values."""
        selected_shapes = self._selected_annotation_shapes()
        if len(selected_shapes) != 1:
            hint = "请选择一个标注后编辑属性。" if not selected_shapes else "一次只能编辑一个标注的属性。"
            self.attributeEditor.clear(hint)
            return
        shape = selected_shapes[0]
        self.attributeEditor.set_annotation(
            shape,
            self.attribute_store.global_attributes(),
            self.attribute_store.attributes_for(getattr(shape, "label", "")),
            self.class_list,
        )

    def on_shape_attributes_changed(self, shape):
        """Include property edits in autosave and undo/redo snapshots."""
        if shape and shape.scene() is self.scene:
            self._refresh_shape_display_label(shape)
            self.scene.state_changed.emit()

    def rename_attribute_label(self, shape, attribute_id, scope):
        """Rename a schema node directly from the selected shape's right pane."""
        if not self._can_modify_attribute_schema():
            return
        class_name = getattr(shape, "label", "")
        schema = (
            self.attribute_store.global_attributes()
            if scope == "global"
            else self.attribute_store.attributes_for(class_name)
        )

        def find_node(nodes):
            for node in nodes:
                if node.get("id") == attribute_id:
                    return node
                found = find_node(node.get("children", []))
                if found:
                    return found
            return None

        node = find_node(schema)
        if not node:
            return
        new_name, accepted = QInputDialog.getText(
            self,
            "修改属性标签",
            "属性标签：",
            text=node.get("name", ""),
        )
        new_name = new_name.strip()
        if not accepted or not new_name or new_name == node.get("name"):
            return
        previous_schema = deepcopy(schema)
        node["name"] = new_name
        if scope == "global":
            self.attribute_store.set_global_attributes(schema)
        else:
            self.attribute_store.set_attributes(class_name, schema)
        try:
            self.attribute_store.save()
        except Exception as exc:
            if scope == "global":
                self.attribute_store.set_global_attributes(previous_schema)
            else:
                self.attribute_store.set_attributes(class_name, previous_schema)
            QMessageBox.warning(self, "属性修改失败", f"未完成属性修改，原配置已保留：\n{exc}")
            return
        self.update_annotation_tree()
        self.update_attribute_editor()

    def _prune_removed_attribute_values(self, class_name=None, removed_ids=None):
        """Remove values whose definitions were deleted from a class schema.

        This keeps the schema and JSON payloads aligned, instead of leaving
        invisible metadata behind after an attribute or subtree is removed.
        """
        removed_ids = set(removed_ids or ())
        if not removed_ids:
            return
        changed_current = False
        for shape in [
            item for item in self.scene.items()
            if isinstance(item, (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape))
            and not getattr(item, "is_temp", False)
            and (class_name is None or getattr(item, "label", "") == class_name)
        ]:
            attributes = getattr(shape, "attributes", {})
            if not isinstance(attributes, dict):
                shape.attributes = {}
                changed_current = True
                continue
            filtered = {key: value for key, value in attributes.items() if key not in removed_ids}
            if filtered != attributes:
                shape.attributes = filtered
                changed_current = True

        annotation_dir = self.annotation_output_dir or self.current_dir
        errors = []
        image_stems = self._dataset_image_stems()
        if annotation_dir and os.path.isdir(annotation_dir):
            import glob
            for json_path in glob.glob(os.path.join(annotation_dir, "*.json")):
                if (os.path.basename(json_path) in {"class_colors.json", "class_attributes.json"}
                        or os.path.normcase(os.path.splitext(os.path.basename(json_path))[0]) not in image_stems):
                    continue
                try:
                    read_fingerprint = file_fingerprint(json_path)
                    with open(json_path, "r", encoding="utf-8") as stream:
                        data = json.load(stream, parse_constant=_reject_json_constant)
                    if not isinstance(data, dict) or not isinstance(data.get("shapes"), list):
                        raise ValueError("JSON 标注必须包含 shapes 数组")
                    changed_file = False
                    for shape_data in data.get("shapes", []):
                        shape_label = shape_data.get("label", "")
                        if class_name is not None and shape_label != class_name:
                            continue
                        attributes = shape_data.get("attributes", {})
                        if not isinstance(attributes, dict):
                            raise ValueError("标注 attributes 不是对象")
                        filtered = {key: value for key, value in attributes.items() if key not in removed_ids}
                        if filtered != attributes:
                            shape_data["attributes"] = filtered
                            changed_file = True
                    if changed_file:
                        self._protect_dataset_sidecar_write(json_path, read_fingerprint)
                        Exporter._atomic_write_text(json_path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
                        self._remember_known_annotation_revision(json_path)
                except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as exc:
                    print(f"清理已删除属性失败: {json_path}, {exc}")
                    errors.append(f"{json_path}: {exc}")

        if changed_current:
            self.scene.state_changed.emit()
        if errors:
            DialogOver(self, "部分标注属性未更新，请检查自动备份：\n"
                       + getattr(self, "_last_category_backup", "") + "\n\n" + "\n".join(errors), "部分更新失败", "warning")

    def schedule_annotation_tree_refresh(self):
        """Merge repeated scene notifications into one tree refresh per event loop."""
        if not self._annotation_tree_timer.isActive():
            self._annotation_tree_timer.start()

    def _refresh_shape_display_label(self, shape, global_schema=None, class_schema=None):
        if getattr(shape, "is_temp", False) or not hasattr(shape, "label_text"):
            return
        label = getattr(shape, "label", "")
        shape.update_label_text(
            label,
            self.attribute_store.global_attributes() if global_schema is None else global_schema,
            self.attribute_store.attributes_for(label) if class_schema is None else class_schema,
        )

    def update_annotation_tree(self):
        self._annotation_tree_timer.stop()
        shapes = []
        global_schema = self.attribute_store.global_attributes()
        class_schemas = {}
        for item in self.scene.items():
            from jotmark.graphics.shapes import RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape
            if isinstance(item, (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape)) and not getattr(item, 'is_temp', False):
                label = getattr(item, "label", "")
                if label not in class_schemas:
                    class_schemas[label] = self.attribute_store.attributes_for(label)
                self._refresh_shape_display_label(item, global_schema, class_schemas[label])
                shapes.append(item)
        self.classListWidget.update_annotations(shapes)

    def sync_selection_to_tree(self):
        from jotmark.graphics.shapes import RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape
        selected_shapes = [item for item in self.scene.selectedItems() if isinstance(item, (RectShape, PolyShape, PolylineShape, PointShape, RotatedRectShape, PoseShape)) and not getattr(item, 'is_temp', False)]
        if self.sam_merge_selection_mode:
            self.classListWidget.select_items_by_shapes(selected_shapes)
        elif selected_shapes:
            self.classListWidget.select_item_by_shape(selected_shapes[0])
        else:
            self.classListWidget.clear_tree_selection()

    def on_select_all_toggled(self, state):
        self.listFiles.blockSignals(True)
        # 兼容 PySide6 整数和 CheckState 枚举比较
        is_unchecked = (state == 0 or state == Qt.Unchecked)
        if is_unchecked:
            check_state = Qt.Unchecked
        else:
            check_state = Qt.Checked
            # 强制设为 Checked 状态（以防万一）
            self.chkSelectAll.blockSignals(True)
            self.chkSelectAll.setCheckState(Qt.Checked)
            self.chkSelectAll.blockSignals(False)
        for i in range(self.listFiles.count()):
            item = self.listFiles.item(i)
            if item:
                item.setCheckState(check_state)
        self.listFiles.blockSignals(False)
        self.update_selected_count()

    def on_file_item_changed(self, item):
        self.update_selected_count()

    def update_selected_count(self):
        total = self.listFiles.count()
        checked = 0
        for i in range(total):
            item = self.listFiles.item(i)
            if item and item.checkState() == Qt.Checked:
                checked += 1
                
        self.labelSelectedCount.setText(f"✓ {checked}/{total}")
        self.labelSelectedCount.setToolTip(f"已选文件：{checked}/{total}")
        
        # 动态控制可用性
        has_items = total > 0
        self.chkSelectAll.setEnabled(has_items)
        self.btnDeleteFiles.setEnabled(has_items)
        
        self.chkSelectAll.blockSignals(True)
        if total == 0:
            self.chkSelectAll.setCheckState(Qt.Unchecked)
        elif checked == 0:
            self.chkSelectAll.setCheckState(Qt.Unchecked)
        elif checked == total:
            self.chkSelectAll.setCheckState(Qt.Checked)
        else:
            self.chkSelectAll.setCheckState(Qt.PartiallyChecked)
        self.chkSelectAll.setTristate(False)
        self.chkSelectAll.blockSignals(False)
        self.update_image_navigation()
        self.update_annotation_status()

    def update_image_navigation(self):
        current_idx = self.listFiles.currentRow()
        total = self.listFiles.count()
        self.btnPreviousImage.setEnabled(current_idx > 0)
        self.btnNextImage.setEnabled(0 <= current_idx < total - 1)

    @staticmethod
    def _file_item_path(item):
        """Return an image path without relying on the visible list text."""
        if not item:
            return ""
        return item.data(Qt.UserRole) or item.text()

    def _file_item_display_name(self, image_path):
        filename = os.path.basename(image_path)
        if self.show_file_extensions:
            return filename
        return os.path.splitext(filename)[0]

    def _create_file_list_item(self, image_path):
        """Create a file-list row whose text is presentation-only.

        The absolute path belongs in ``Qt.UserRole`` so changing the visible
        name/extension never breaks loading, auto-save, export, or deletion.
        """
        image_path = os.path.abspath(image_path)
        item = QListWidgetItem(self._file_item_display_name(image_path))
        item.setData(Qt.UserRole, image_path)
        item.setToolTip(image_path)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        return item

    @staticmethod
    def _natural_filename_key(image_path):
        """Sort filenames naturally: image_2 comes before image_10."""
        filename = os.path.basename(image_path).casefold()
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in re.split(r"(\d+)", filename)
        )

    def _sort_image_paths(self, image_paths):
        """Return paths using the selected persistent file-list ordering."""
        def metadata_key(path):
            try:
                if self.file_sort_key == "modified":
                    return (os.path.getmtime(path), self._natural_filename_key(path))
                if self.file_sort_key == "size":
                    return (os.path.getsize(path), self._natural_filename_key(path))
            except OSError:
                pass
            return self._natural_filename_key(path)

        return sorted(image_paths, key=metadata_key, reverse=self.file_sort_descending)

    def _rebuild_file_list(self, image_paths, preferred_path=""):
        """Reorder files without losing checked state or the current image."""
        existing_state = {}
        for index in range(self.listFiles.count()):
            item = self.listFiles.item(index)
            if item:
                existing_state[self._file_item_path(item)] = item.checkState()
        current_path = preferred_path or self._file_item_path(self.listFiles.currentItem())
        selected_paths = {self._file_item_path(item) for item in self.listFiles.selectedItems()}
        sorted_paths = self._sort_image_paths(image_paths)

        signals_were_blocked = self.listFiles.blockSignals(True)
        try:
            self.listFiles.clear()
            for image_path in sorted_paths:
                item = self._create_file_list_item(image_path)
                if image_path in existing_state:
                    item.setCheckState(existing_state[image_path])
                if image_path in selected_paths:
                    item.setSelected(True)
                self.listFiles.addItem(item)
        finally:
            self.listFiles.blockSignals(signals_were_blocked)

        if self.listFiles.count():
            target_row = 0
            for index in range(self.listFiles.count()):
                if self._file_item_path(self.listFiles.item(index)) == current_path:
                    target_row = index
                    break
            self.listFiles.setCurrentRow(target_row)
        self.update_selected_count()

    def _directory_image_paths(self, directory):
        if not directory or not os.path.isdir(directory):
            return []
        return [
            os.path.join(directory, filename)
            for filename in os.listdir(directory)
            if filename.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)
        ]

    def _update_file_sort_button(self):
        if not hasattr(self, "btnFileSort"):
            return
        labels = {"name": "文件名", "modified": "修改时间", "size": "文件大小"}
        direction = "降序" if self.file_sort_descending else "升序"
        self.btnFileSort.setToolTip(
            f"当前：按{labels.get(self.file_sort_key, '文件名')}{direction}排序\n点击可更改排序方式"
        )

    def show_file_sort_menu(self):
        """Offer compact sorting controls for the file browser."""
        menu = QMenu(self)
        for sort_key, label in (("name", "按文件名"), ("modified", "按修改时间"), ("size", "按文件大小")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.file_sort_key == sort_key)
            action.setData(("key", sort_key))
        menu.addSeparator()
        ascending = menu.addAction("升序")
        ascending.setCheckable(True)
        ascending.setChecked(not self.file_sort_descending)
        ascending.setData(("direction", False))
        descending = menu.addAction("降序")
        descending.setCheckable(True)
        descending.setChecked(self.file_sort_descending)
        descending.setData(("direction", True))

        action = menu.exec(self.btnFileSort.mapToGlobal(self.btnFileSort.rect().bottomLeft()))
        if not action or not action.data():
            return
        kind, value = action.data()
        if kind == "key":
            self.file_sort_key = value
        else:
            self.file_sort_descending = bool(value)
        settings = QSettings("luohuabuxiema", "JotMark")
        settings.setValue("file_sort_key", self.file_sort_key)
        settings.setValue("file_sort_descending", self.file_sort_descending)
        self._update_file_sort_button()
        self._rebuild_file_list([
            self._file_item_path(self.listFiles.item(index))
            for index in range(self.listFiles.count())
            if self.listFiles.item(index) is not None
        ])

    def _update_file_extension_button(self):
        if not hasattr(self, "btnFileExtensions"):
            return
        state = "显示" if self.show_file_extensions else "隐藏"
        next_action = "隐藏" if self.show_file_extensions else "显示"
        self.btnFileExtensions.setToolTip(f"当前：{state}图片文件后缀；点击可{next_action}")

    def set_file_extension_visible(self, visible):
        """Toggle filename extensions while preserving file-list state."""
        self.show_file_extensions = bool(visible)
        QSettings("luohuabuxiema", "JotMark").setValue(
            "show_file_extensions", self.show_file_extensions
        )
        self._update_file_extension_button()

        signals_were_blocked = self.listFiles.blockSignals(True)
        try:
            for index in range(self.listFiles.count()):
                item = self.listFiles.item(index)
                image_path = self._file_item_path(item)
                if image_path:
                    item.setText(self._file_item_display_name(image_path))
        finally:
            self.listFiles.blockSignals(signals_were_blocked)

    def annotation_output_path(self, image_path, format_type=None):
        """Return the sidecar annotation path for an image in the active output directory."""
        format_type = format_type or self.current_format
        extension = ANNOTATION_EXTENSION_BY_FORMAT.get(format_type)
        if not extension:
            raise ValueError(f"不支持的标注格式: {format_type}")
        output_dir = self.annotation_output_dir or os.path.dirname(image_path)
        image_name = os.path.splitext(os.path.basename(image_path))[0]
        return os.path.join(output_dir, image_name + extension)

    def update_annotation_output_dir_ui(self):
        if self.annotation_output_dir:
            self.btnAnnotationOutputDir.setText("保存目录 ✓")
            self.btnAnnotationOutputDir.setToolTip(f"标注保存目录：{self.annotation_output_dir}\n点击可更换目录")
        else:
            self.btnAnnotationOutputDir.setText("保存目录")
            self.btnAnnotationOutputDir.setToolTip("标注保存目录：图片同目录（默认）\n点击可选择统一输出目录")

    def choose_annotation_output_dir(self):
        start_dir = self.annotation_output_dir or self.current_dir or ""
        output_dir = QFileDialog.getExistingDirectory(self, "选择标注保存目录", start_dir)
        if not output_dir:
            return
        self.set_annotation_output_dir(output_dir)

    def set_annotation_output_dir(self, output_dir):
        if output_dir == self.annotation_output_dir:
            return
        # Preserve any work currently shown in the old location before
        # switching reads and writes to the newly selected output directory.
        if self.current_image_path and not self._ensure_pending_annotation_saved():
            return
        self.annotation_output_dir = output_dir
        QSettings("luohuabuxiema", "JotMark").setValue("annotation_output_dir", output_dir)
        self.update_annotation_output_dir_ui()
        self.update_annotation_status()
        if self.current_image_path:
            self.scene.clear_shapes()
            self.load_annotations(self.current_image_path)
            self.apply_class_colors_to_scene()
            self.update_annotation_tree()
            self._remember_current_annotation_signature()
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._undo_signatures.clear()
            self._redo_signatures.clear()
            self.push_state()
        self.statusBar.showMessage(f"标注将保存至：{output_dir or '图片同目录'}", 5000)

    def show_settings_dialog(self):
        dialog = SettingsDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        width = dialog.line_width.value()
        if width != self.scene.annotation_line_width:
            self.scene.set_annotation_line_width(width)
            QSettings("luohuabuxiema", "JotMark").setValue("annotation_line_width", width)
        show_attributes = dialog.canvas_attributes.isChecked()
        if show_attributes != self.scene.show_canvas_attributes:
            self.scene.show_canvas_attributes = show_attributes
            QSettings("luohuabuxiema", "JotMark").setValue("show_canvas_attributes", show_attributes)
            self.update_annotation_tree()
        transparent = dialog.transparent_label_background.isChecked()
        if transparent != self.scene.transparent_label_background:
            self.scene.set_transparent_label_background(transparent)
            QSettings("luohuabuxiema", "JotMark").setValue("transparent_label_background", transparent)
        margin = dialog.edge_click_margin.value()
        if margin != self.scene.edge_click_margin:
            self.scene.edge_click_margin = margin
            QSettings("luohuabuxiema", "JotMark").setValue("edge_click_margin", margin)
            if not self.view._user_zoomed:
                self.view.reset_to_fit()
        if dialog.extensions.isChecked() != self.show_file_extensions:
            self.btnFileExtensions.setChecked(dialog.extensions.isChecked())
        key = dialog.sort_key.currentData()
        descending = dialog.descending.isChecked()
        if key != self.file_sort_key or descending != self.file_sort_descending:
            self.file_sort_key = key
            self.file_sort_descending = descending
            settings = QSettings("luohuabuxiema", "JotMark")
            settings.setValue("file_sort_key", key)
            settings.setValue("file_sort_descending", descending)
            self._update_file_sort_button()
            self._rebuild_file_list([
                self._file_item_path(self.listFiles.item(index))
                for index in range(self.listFiles.count())
                if self.listFiles.item(index) is not None
            ])
        if dialog.dark_theme.isChecked() != self.is_dark_theme:
            self.toggle_theme()
        if dialog.output_dir.text() != self.annotation_output_dir:
            self.set_annotation_output_dir(dialog.output_dir.text())

    def update_annotation_status(self):
        total = self.listFiles.count()
        annotated = 0
        signals_were_blocked = self.listFiles.blockSignals(True)
        try:
            for index in range(total):
                item = self.listFiles.item(index)
                if not item:
                    continue
                image_path = self._file_item_path(item)
                saved_paths = {
                    format_type: self.annotation_output_path(image_path, format_type)
                    for format_type in ANNOTATION_EXTENSION_BY_FORMAT
                }
                existing_formats = [
                    format_type for format_type, path in saved_paths.items()
                    if os.path.isfile(path)
                ]
                existing_paths = [saved_paths[format_type] for format_type in existing_formats]
                if existing_paths:
                    annotated += 1
                    item.setForeground(QBrush(QColor("#16A34A")))
                    format_labels = {
                        "json": "JSON",
                        "yolo": "YOLO",
                        "xml": "XML",
                        "curvelanes": "CurveLanes",
                    }
                    formats = ", ".join(format_labels[format_type] for format_type in existing_formats)
                    item.setToolTip(f"图片：{image_path}\n已标注（{formats}）：{existing_paths[0]}")
                else:
                    item.setForeground(QBrush())
                    item.setToolTip(image_path)
        finally:
            self.listFiles.blockSignals(signals_were_blocked)
        self.labelAnnotatedCount.setText(f"● {annotated}/{total}")
        self.labelAnnotatedCount.setToolTip(f"已标注图片：{annotated}/{total}")

    def open_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择图片目录", self.current_dir or "")
        if dir_path:
            if not self._ensure_pending_annotation_saved():
                return
            self.current_dir = dir_path
            self.load_classes(dir_path)
            self._rebuild_file_list(self._directory_image_paths(dir_path))

    # def update_model_status(self, success, msg):
    #     self.helpLabel.setText(msg)
    #     if success:
    #         self.helpLabel.setStyleSheet("color: green;")
    #     else:
    #         self.helpLabel.setStyleSheet("color: red;")

    def update_model_status(self, success, msg):
        message = str(msg or "")
        if success:
            self.clear_runtime_error()
            self.helpLabel.setText(message)
            self.helpLabel.setStyleSheet("color: green;")
            sam_ready = self._sam_model_is_ready()
            # A SAM model selected through the explicit button activates
            # Smart mode only after it genuinely finished loading.
            if sam_ready and self._enable_sam_after_model_load:
                self._enable_sam_after_model_load = False
                self.samSwitch.setChecked(True)
            # A loaded SAM model can encode the current image.  YOLO and the
            # no-model manual state deliberately skip this expensive step.
            if sam_ready and self.current_image_path:
                self.request_sam_image_encoding(self.current_image_path)
        else:
            # Loading progress is intentionally emitted as a False status. It
            # is not an error and should not expose the detail button.
            if "正在加载" in message or "请稍候" in message:
                self.clear_runtime_error()
                self.helpLabel.setText(message)
                self.helpLabel.setStyleSheet("color: orange;")
            else:
                self._enable_sam_after_model_load = False
                self.set_runtime_error("模型加载失败", message)
        self.update_sam_merge_button()

    def request_sam_image_encoding(self, image_path):
        """Schedule SAM image features without blocking image navigation/UI."""
        request_id = self.sam_client.set_image(image_path)
        self._sam_image_request_id = request_id
        if request_id is not None:
            self.helpLabel.setText("正在后台解析图片特征，可继续浏览或编辑标注...")
            self.helpLabel.setStyleSheet("color: orange;")
        self.update_sam_merge_button()

    def on_sam_image_ready(self, request_id, success, error_message):
        """Only surface the result for the image currently shown in the UI."""
        if request_id != self._sam_image_request_id:
            return
        self._sam_image_request_id = None
        if success:
            self.clear_runtime_error()
            self.helpLabel.setStyleSheet("color: green;")
            self._update_help_text(self.scene.mode)
        else:
            self.set_runtime_error("图片特征解析失败", error_message)
        self.update_sam_merge_button()

    @staticmethod
    def _runtime_error_summary(message, max_length=100):
        """Produce a single-line status-bar summary for a verbose exception."""
        message = " ".join(str(message or "未知错误").split())
        lowered = message.lower()
        if "out of memory" in lowered or "cuda oom" in lowered:
            return "CUDA 显存不足"
        if len(message) > max_length:
            return message[:max_length - 1].rstrip() + "…"
        return message

    def set_runtime_error(self, context, message):
        """Show a bounded error summary and retain complete copyable details."""
        context = str(context or "运行失败")
        detail = str(message or "未知错误")
        self._last_runtime_error = f"{context}\n\n{detail}"
        summary = self._runtime_error_summary(detail)
        self.helpLabel.setText(f"{context}：{summary}（点击“错误详情”复制）")
        self.helpLabel.setToolTip(self.helpLabel.text())
        self.helpLabel.setStyleSheet("color: #DC2626;")
        self.btnErrorDetails.show()

    def clear_runtime_error(self):
        self._last_runtime_error = ""
        if hasattr(self, "btnErrorDetails"):
            self.btnErrorDetails.hide()

    def show_runtime_error_details(self):
        """Open a selectable/copyable dialog without affecting main-window size."""
        if not self._last_runtime_error:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("JotMark 错误详情")
        dialog.resize(760, 440)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hint = QLabel("以下信息可复制后发送给维护者。")
        layout.addWidget(hint)
        detail_box = QPlainTextEdit()
        detail_box.setReadOnly(True)
        detail_box.setPlainText(self._last_runtime_error)
        detail_box.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(detail_box, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        copy_button = QPushButton("复制完整错误")
        close_button = QPushButton("关闭")
        buttons.addWidget(copy_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def copy_error():
            QApplication.clipboard().setText(self._last_runtime_error)
            copy_button.setText("已复制")

        copy_button.clicked.connect(copy_error)
        close_button.clicked.connect(dialog.accept)
        dialog.exec()

    def show_file_list_context_menu(self, pos):
        """显示文件列表右键菜单"""
        selected_items = self.listFiles.selectedItems()
        if not selected_items:
            return

        menu = QMenu()
        # 应用主题样式
        if self.is_dark_theme:
            menu.setStyleSheet("QMenu { background-color: #2b2b2b; color: #fff; border: 1px solid #444; }")
        
        count = len(selected_items)
        delete_action = menu.addAction(f"移至回收站（选中 {count} 个文件）")
        delete_action.setIcon(QIcon("ui/icon/trash.svg"))
        
        action = menu.exec(self.listFiles.mapToGlobal(pos))
        if action == delete_action:
            self.delete_selected_files(selected_items)

    def delete_selected_files(self, items):
        """Move images and sidecars to a recoverable per-folder trash."""
        if not items or not self._ensure_pending_annotation_saved():
            return
        paths_to_delete = [self._file_item_path(item) for item in items]
        try:
            for path in paths_to_delete:
                ensure_unique_image_stem(path, SUPPORTED_IMAGE_EXTENSIONS)
            trash_dir = os.path.join(
                self.current_dir or os.path.dirname(paths_to_delete[0]),
                ".jotmark_trash",
                datetime.now().strftime("%Y%m%d-%H%M%S"),
            )
            os.makedirs(trash_dir, exist_ok=True)
        except Exception as exc:
            QMessageBox.warning(self, "移动已取消", f"无法安全移动文件：\n{exc}")
            return

        moved_count = 0
        current_deleted = False
        failures = []
        self.listFiles.currentItemChanged.disconnect(self.on_file_selected)
        for item, img_path in zip(items, paths_to_delete):
            moved_paths = []
            try:
                # Preserve the picture and every sidecar together. This makes
                # accidental bulk deletes recoverable without format loss.
                source_paths = [img_path] + [
                    self.annotation_output_path(img_path, format_type)
                    for format_type in ANNOTATION_EXTENSION_BY_FORMAT
                ]
                for source_path in dict.fromkeys(source_paths):
                    if not os.path.exists(source_path):
                        continue
                    destination = os.path.join(trash_dir, os.path.basename(source_path))
                    if os.path.exists(destination):
                        stem, suffix = os.path.splitext(destination)
                        destination = f"{stem}-{datetime.now().strftime('%f')}{suffix}"
                    shutil.move(source_path, destination)
                    moved_paths.append((source_path, destination))

                if img_path == self.current_image_path:
                    self.scene.clear_shapes()
                    if self.scene.img_item:
                        self.scene.removeItem(self.scene.img_item)
                    self.scene.img_item = None
                    self.current_image_path = None
                    current_deleted = True
                
                # 从列表中移除
                row = self.listFiles.row(item)
                self.listFiles.takeItem(row)
                
                moved_count += 1
            except Exception as e:
                failures.append(f"{img_path}: {e}")
                for original, moved in reversed(moved_paths):
                    try:
                        if os.path.exists(original):
                            raise FileExistsError(f"原位置已存在文件：{original}")
                        shutil.move(moved, original)
                    except Exception as rollback_error:
                        failures.append(f"无法自动恢复 {moved}: {rollback_error}")

        # 恢复信号
        self.listFiles.currentItemChanged.connect(self.on_file_selected)
        self.update_selected_count()
        if failures:
            QMessageBox.warning(self, "部分文件未移动", "请检查回收站及原目录：\n" + "\n".join(failures))

        if current_deleted:
            self.statusBar.showMessage(f"已将 {moved_count} 个文件移至回收站，当前预览已清除", 3000)
            # 如果列表还有文件，自动选中并加载第一项或当前项
            if self.listFiles.count() > 0:
                current_row = self.listFiles.currentRow()
                if current_row < 0:
                    current_row = 0
                item = self.listFiles.item(current_row)
                self.listFiles.setCurrentItem(item)
                # 手动触发一次加载
                self.on_file_selected(item, None)
        else:
            self.statusBar.showMessage(f"已将 {moved_count} 个文件移至回收站", 3000)

    def delete_checked_files(self):
        # Find all checked items
        checked_items = []
        for i in range(self.listFiles.count()):
            item = self.listFiles.item(i)
            if item and item.checkState() == Qt.Checked:
                checked_items.append(item)
                
        if not checked_items:
            DialogOver(self, "请先勾选要移至回收站的文件！", "提示", "warning")
            return
            
        # Ask for confirmation
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, 
            "移至回收站",
            f"确定要将勾选的 {len(checked_items)} 个图片及其标注文件移至 .jotmark_trash 吗？可手动恢复。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.delete_selected_files(checked_items)

    def on_file_selected(self, current, previous):
        self.update_image_navigation()
        if current and self._file_item_path(current) == self.current_image_path:
            return
        if self.current_image_path and not self._ensure_pending_annotation_saved():
            blocked = self.listFiles.blockSignals(True)
            try:
                if previous and self.listFiles.row(previous) >= 0:
                    self.listFiles.setCurrentItem(previous)
            finally:
                self.listFiles.blockSignals(blocked)
            self.update_image_navigation()
            return

        if current:
            path = self._file_item_path(current)
            self.current_image_path = path
            self.scene.load_image(path)
            self.view.reset_to_fit()
            self.load_annotations(path)
            self.apply_class_colors_to_scene()
            self.update_annotation_tree()
            self._remember_current_annotation_signature()

            self.undo_stack.clear()
            self.redo_stack.clear()
            self._undo_signatures.clear()
            self._redo_signatures.clear()
            self.push_state()
            
            # 清除旧图片的骨架预览
            if hasattr(self.scene, 'pose_preview_item') and self.scene.pose_preview_item:
                self.scene.removeItem(self.scene.pose_preview_item)
                self.scene.pose_preview_item = None

            # 只有SAM模型才需要后台分析图片特征
            is_sam = getattr(self.sam_client, 'current_model_key', '') and getattr(self.sam_client, 'current_model_key', '').startswith('sam')
            if is_sam:
                if self.sam_client.model:
                    self.request_sam_image_encoding(path)
                else:
                    self.helpLabel.setText("等待后台加载模型，稍后将自动分析图片...")
                    self.helpLabel.setStyleSheet("color: orange;")
            else:
                # YOLO 或者没有模型时，恢复默认的标注提示词
                self._update_help_text(self.scene.mode)

    def auto_save_annotation(self, immediate=False):
        """Debounce autosave while retaining immediate flushes at boundaries."""
        if not self.current_image_path or not self.scene.img_item:
            return True
        self._autosave_dirty = True
        if immediate:
            self._autosave_timer.stop()
            return self._flush_auto_save_annotation()
        else:
            self._autosave_timer.start()
        return True

    def _set_save_failure(self, message):
        self._last_autosave_error = str(message)
        self.saveFailureLabel.setToolTip(self._last_autosave_error)
        self.saveFailureLabel.show()
        self.btnRecoverySave.show()
        print(f"标注保存失败，画布尚未保存: {message}")

    def _clear_save_failure(self):
        self._last_autosave_error = ""
        self.saveFailureLabel.hide()
        self.btnRecoverySave.hide()

    def _ensure_pending_annotation_saved(self):
        """Do not discard the current canvas if a pending save has failed."""
        if not self._autosave_dirty:
            return True
        self._autosave_timer.stop()
        if self._flush_auto_save_annotation():
            return True
        QMessageBox.warning(
            self, "标注尚未保存",
            "保存失败，当前操作已取消，画布内容仍保留。请检查磁盘空间、权限或外部文件更改；"
            "也可以点击底部“另存恢复”将画布另存为 JSON。\n\n" + self._last_autosave_error,
        )
        return False

    def save_recovery_snapshot(self):
        """Let the user save a new JSON copy even when the original is protected."""
        if not self.current_image_path or not self.scene.img_item:
            return False
        name = os.path.splitext(os.path.basename(self.current_image_path))[0]
        suggested = os.path.join(os.path.expanduser("~"), f"{name}-JotMark恢复-{datetime.now():%Y%m%d-%H%M%S}.json")
        path, _ = QFileDialog.getSaveFileName(self, "另存恢复标注（不覆盖已有文件）", suggested, "JSON 文件 (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        target = os.path.abspath(self.annotation_output_path(self.current_image_path))
        if target in self._protected_annotation_paths:
            reply = QMessageBox.question(
                self, "原标注读取不完整",
                "当前画布可能只包含原文件的一部分标注。恢复文件也只保存画布中可见的内容，原文件不会修改。继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
        reservation = None
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            reservation = os.stat(path)
            shapes = Exporter.extract_shapes(self.scene)
            size = self.scene.image_rect()
            Exporter.save_json(path, self.current_image_path, size.width(), size.height(), shapes, self._json_document_metadata)
        except Exception as exc:
            if reservation:
                try:
                    current = os.stat(path)
                    if current.st_ino == reservation.st_ino and current.st_dev == reservation.st_dev and current.st_size == 0:
                        os.unlink(path)
                except OSError:
                    pass
            QMessageBox.warning(self, "恢复文件保存失败", str(exc))
            return False
        self._last_recovery_snapshot = (self.current_image_path, self._annotation_signature(shapes), path, file_fingerprint(path))
        QMessageBox.information(
            self, "恢复文件已保存",
            f"恢复文件：\n{path}\n\n原标注位置仍未保存。恢复时请在新目录放入对应图片，并把恢复文件重命名为与图片同名的 .json。",
        )
        return True

    def _check_annotation_target(self, path):
        """Reject ambiguous names, failed loads and externally modified files."""
        key = os.path.abspath(path)
        if key in self._protected_annotation_paths:
            raise RuntimeError("原标注读取不完整，已禁止覆盖：" + self._protected_annotation_paths[key])
        if self.current_image_path:
            ensure_unique_image_stem(self.current_image_path, SUPPORTED_IMAGE_EXTENSIONS)
        actual = file_fingerprint(key)
        if key not in self._annotation_disk_fingerprints:
            if actual is not None:
                raise RuntimeError(f"目标文件未经过本次读取，禁止覆盖：{path}")
            self._annotation_disk_fingerprints[key] = None
        elif actual != self._annotation_disk_fingerprints[key]:
            raise RuntimeError(f"标注文件已被其他程序修改，禁止覆盖：{path}")

    def _write_annotation_data(self, out_path, format_type, shapes_data):
        """Protect the prior revision and write or archive one selected format."""
        self._check_annotation_target(out_path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        empty = not shapes_data or (format_type == "curvelanes" and not self._curvelanes_polyline_count(shapes_data))
        if empty:
            archived = archive_annotation(out_path)
        else:
            archived = None
            backup_previous_annotation(out_path)
            if file_fingerprint(out_path) != self._annotation_disk_fingerprints[os.path.abspath(out_path)]:
                raise RuntimeError(f"备份期间标注文件被其他程序修改，禁止覆盖：{out_path}")
            img_rect = self.scene.image_rect()
            if format_type == "json":
                Exporter.save_json(out_path, self.current_image_path, img_rect.width(), img_rect.height(), shapes_data, self._json_document_metadata)
            elif format_type == "yolo":
                Exporter.save_yolo(out_path, img_rect.width(), img_rect.height(), shapes_data, self.class_list)
            elif format_type == "xml":
                Exporter.save_xml(out_path, self.current_image_path, img_rect.width(), img_rect.height(), shapes_data)
            elif format_type == "curvelanes":
                Exporter.save_curvelanes(out_path, shapes_data)
            else:
                raise ValueError(f"不支持的标注格式: {format_type}")
        self._annotation_disk_fingerprints[os.path.abspath(out_path)] = file_fingerprint(out_path)
        return archived

    def _annotation_signature(self, shapes_data):
        return json.dumps(shapes_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _curvelanes_polyline_count(shapes_data):
        """Return the number of valid open polylines for CondLaneNet export."""
        return sum(
            1
            for shape in shapes_data
            if shape.get("type") == "linestrip" and len(shape.get("points", [])) >= 2
        )

    def _remember_current_annotation_signature(self):
        if not self.current_image_path or not self.scene.img_item:
            return
        out_path = self.annotation_output_path(self.current_image_path)
        self._saved_annotation_signatures[out_path] = self._annotation_signature(Exporter.extract_shapes(self.scene))

    def _flush_auto_save_annotation(self):
        if not self._autosave_dirty or not self.current_image_path or not self.scene.img_item:
            return not self._autosave_dirty
        try:
            shapes_data = Exporter.extract_shapes(self.scene)
            out_path = self.annotation_output_path(self.current_image_path)
            signature = self._annotation_signature(shapes_data)
            if self._saved_annotation_signatures.get(out_path) != signature:
                self._write_annotation_data(out_path, self.current_format, shapes_data)
            self._saved_annotation_signatures[out_path] = signature
            self._autosave_dirty = False
            self._clear_save_failure()
            self.update_annotation_status()
            return True
        except Exception as exc:
            self._set_save_failure(exc)
            return False

    def set_current_format(self, format_type):
        if format_type == self.current_format:
            self.formatWidget.set_format(format_type)
            return
        if not self._ensure_pending_annotation_saved():
            self.formatWidget.set_format(self.current_format)
            return
        self.current_format = format_type
        self.formatWidget.set_format(format_type)

        # if format_type == "json":
        #     self.actionFormatJSON.setChecked(True)
        # elif format_type == "yolo":
        #     self.actionFormatYOLO.setChecked(True)
        # elif format_type == "xml":
        #     self.actionFormatXML.setChecked(True)

        if self.current_image_path:
            self.scene.clear_shapes()
            self.load_annotations(self.current_image_path)
            self.apply_class_colors_to_scene()
            self.update_annotation_tree()
            self._remember_current_annotation_signature()
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._undo_signatures.clear()
            self._redo_signatures.clear()
            self.push_state()
        self.update_annotation_status()
        if format_type == "curvelanes":
            DialogOver(
                self,
                "当前为 CurveLanes / CondLaneNet 格式：仅保存开放折线为 .lines.txt；类别和属性不会写入此训练格式。",
                "格式切换",
                "info",
            )
        else:
            DialogOver(self, f"当前保存及读取格式变为 {format_type.upper()}", "格式切换", "info")

    def load_annotations(self, image_path):
        if not self.scene.img_item: return

        self._json_document_metadata = {}
        image_size = self.scene.image_size()
        img_w, img_h = image_size.width(), image_size.height()
        annotation_path = self.annotation_output_path(image_path, self.current_format)
        key = os.path.abspath(annotation_path)
        self._protected_annotation_paths.pop(key, None)
        try:
            before = file_fingerprint(annotation_path)
            self._annotation_disk_fingerprints[key] = before
        except Exception as exc:
            self._record_annotation_load_error(annotation_path, exc)
            return
        if self.current_format == "json":
            self._load_json(annotation_path)
        elif self.current_format == "yolo":
            self._load_yolo(annotation_path, img_w, img_h)
        elif self.current_format == "xml":
            self._load_xml(annotation_path)
        elif self.current_format == "curvelanes":
            self._load_curvelanes(annotation_path)
        try:
            if file_fingerprint(annotation_path) != before:
                self._record_annotation_load_error(annotation_path, "读取期间文件发生变化")
        except Exception as exc:
            self._record_annotation_load_error(annotation_path, exc)

    def _record_annotation_load_error(self, path, error):
        detail = f"{path}\n{error}"
        self._protected_annotation_paths[os.path.abspath(path)] = str(error)
        self.set_runtime_error("标注读取不完整，已禁止覆盖原文件", detail)
        print(f"标注读取不完整，原文件已保护: {detail}")

    def _add_shape_to_scene(self, shape, label):
        """往画板内添加加载出来的轮廓并同步历史类别"""
        if label not in self.class_list:
            # self.class_list.append(label)
            # self.listClasses.addItem(label)
            self.add_class_to_list(label)
            self.save_classes()
        self.scene.addItem(shape)
        if isinstance(shape, RotatedRectShape):
            shape.constrain_to_scene()
        if hasattr(shape, 'set_color'):
            shape.set_color(self.classListWidget.get_class_color(label))

    def _load_json(self, json_path):
        if not os.path.exists(json_path): return
        try:
            loaded_shapes = []
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f, parse_constant=_reject_json_constant)
            if not isinstance(data, dict) or not isinstance(data.get("shapes"), list):
                raise ValueError("JSON 标注必须包含 shapes 数组")
            _verify_json_image_identity(data, self.current_image_path)
            self._json_document_metadata = deepcopy({
                key: value for key, value in data.items()
                if key not in {"shapes", "imagePath", "imageHeight", "imageWidth"}
            })
            for index, shape_data in enumerate(data["shapes"], start=1):
                if not isinstance(shape_data, dict):
                    raise ValueError(f"第 {index} 个标注不是对象")
                label = shape_data.get("label", "")
                points = shape_data.get("points", [])
                shape_type = shape_data.get("shape_type", "rectangle")
                if not isinstance(points, list):
                    raise ValueError(f"第 {index} 个标注的 points 必须是数组")
                for point in points:
                    if (not isinstance(point, (list, tuple)) or len(point) != 2
                            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point)):
                        raise ValueError(f"第 {index} 个标注包含无效坐标")

                if shape_type == "rectangle" and len(points) == 2:
                    rect = QRectF(points[0][0], points[0][1], points[1][0] - points[0][0], points[1][1] - points[0][1])
                    shape = RectShape(rect, label)
                elif shape_type == "polygon" and len(points) >= 3:
                    qpoints = [QPointF(p[0], p[1]) for p in points]
                    shape = PolyShape(QPolygonF(qpoints), label)
                elif shape_type in ("linestrip", "polyline", "line") and len(points) >= 2:
                    qpoints = [QPointF(p[0], p[1]) for p in points]
                    shape = PolylineShape(QPolygonF(qpoints), label)
                elif shape_type == "point" and len(points) == 1:
                    shape = PointShape(QPointF(points[0][0], points[0][1]), label)
                # 旋转框 (OBB) 的解析分支
                elif shape_type == "obb":
                    rect_data = shape_data.get("rect")
                    angle = shape_data.get("angle", 0)
                    if rect_data and len(rect_data) == 4:
                        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in [*rect_data, angle]):
                            raise ValueError(f"第 {index} 个旋转框包含无效数值")
                        cx, cy, w, h = rect_data[0], rect_data[1], rect_data[2], rect_data[3]
                        shape = RotatedRectShape(cx, cy, w, h, angle, label)
                    else:
                        raise ValueError(f"第 {index} 个旋转框缺少有效 rect")
                elif shape_type == "pose":
                    rect_data = shape_data.get("rect")
                    template_name = shape_data.get("template_name", "")
                    kps_data = shape_data.get("keypoints", [])
                    if rect_data and len(rect_data) == 4:
                        cx, cy, w, h = rect_data
                        angle = shape_data.get("angle", 0)
                        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in [*rect_data, angle]):
                            raise ValueError(f"第 {index} 个姿态标注包含无效数值")
                        if not isinstance(kps_data, list) or any(
                            not isinstance(kp, (list, tuple)) or len(kp) != 3 or any(
                                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in kp
                            ) for kp in kps_data
                        ):
                            raise ValueError(f"第 {index} 个姿态标注包含无效关键点")
                        template = self.template_manager.get_template(template_name)
                        if template and len(template.get("keypoints", [])) != len(kps_data):
                            raise ValueError(f"第 {index} 个姿态标注的关键点数量与模板不一致")
                        if not template:
                            # 降级处理，或者用空模板
                            template = {"name": template_name, "keypoints": [], "connections": []}
                            for i, kp in enumerate(kps_data):
                                template["keypoints"].append({"name": f"kp_{i}", "color": "#00FF00", "default_pos": [0.5, 0.5]})
                        
                        shape = PoseShape(QRectF(cx - w / 2, cy - h / 2, w, h), template, label)
                        shape.setRotation(angle)
                        # 设置读取的关键点位置和可见性
                        for i, kp_data in enumerate(kps_data):
                            if i < len(shape.kps):
                                local_pt = shape.mapFromScene(QPointF(kp_data[0], kp_data[1]))
                                shape.kps[i].setPos(local_pt)
                                shape.kps[i].set_visibility(kp_data[2])
                        shape.update_lines()
                    else:
                        raise ValueError(f"第 {index} 个姿态标注缺少有效 rect")
                else:
                    raise ValueError(f"第 {index} 个标注的类型或坐标不受支持：{shape_type}")
                saved_attributes = shape_data.get("attributes", {})
                if not isinstance(saved_attributes, dict):
                    raise ValueError(f"第 {index} 个标注的 attributes 必须是对象")
                shape.attributes = deepcopy(saved_attributes)
                geometry_fields = {"label", "points", "shape_type", "attributes"}
                if shape_type in ("obb", "pose"):
                    geometry_fields.update({"rect", "angle"})
                if shape_type == "pose":
                    geometry_fields.update({"keypoints", "kpt_shape", "template_name"})
                metadata = {key: deepcopy(value) for key, value in shape_data.items() if key not in geometry_fields}
                if metadata.get("group_id", None) is None:
                    metadata.pop("group_id", None)
                if metadata.get("flags", None) == {}:
                    metadata.pop("flags", None)
                shape._json_metadata = metadata
                loaded_shapes.append((shape, label))
            for shape, label in loaded_shapes:
                self._add_shape_to_scene(shape, label)
        except Exception as e:
            self._record_annotation_load_error(json_path, e)

    def _load_yolo(self, txt_path, img_w, img_h):
        if not os.path.exists(txt_path): return
        import math  # 局部导入数学库，用于逆向推导
        try:
            loaded_shapes = []
            with open(txt_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            for line in lines:
                parts = line.strip().split()
                if not parts: continue
                if any(not math.isfinite(float(part)) for part in parts):
                    raise ValueError("YOLO 标注包含无效数值")
                class_id = int(parts[0])
                if class_id < 0:
                    raise ValueError("YOLO 类别编号不能为负数")
                label = self.class_list[class_id] if class_id < len(self.class_list) else str(class_id)
                shape = None

                # 1. YOLO BBox 格式 (常规矩形：5个参数)
                if len(parts) == 5:
                    cx, cy = float(parts[1]) * img_w, float(parts[2]) * img_h
                    w, h = float(parts[3]) * img_w, float(parts[4]) * img_h
                    shape = RectShape(QRectF(cx - w / 2, cy - h / 2, w, h), label)

                # YOLO OBB 旋转框格式 (9个参数：1 个类别 + 8 个坐标)
                elif len(parts) == 9:
                    x1, y1 = float(parts[1]) * img_w, float(parts[2]) * img_h
                    x2, y2 = float(parts[3]) * img_w, float(parts[4]) * img_h
                    x3, y3 = float(parts[5]) * img_w, float(parts[6]) * img_h
                    x4, y4 = float(parts[7]) * img_w, float(parts[8]) * img_h

                    # 利用四边形的顶点逆向推导出原生属性
                    cx = (x1 + x2 + x3 + x4) / 4.0
                    cy = (y1 + y2 + y3 + y4) / 4.0
                    w = math.hypot(x2 - x1, y2 - y1)
                    h = math.hypot(x4 - x1, y4 - y1)
                    angle = math.degrees(math.atan2(y2 - y1, x2 - x1))

                    # 重新生成带有完美手柄的 OBB 对象
                    shape = RotatedRectShape(cx, cy, w, h, angle, label)

                # YOLO Pose / Polygon 格式冲突处理
                elif len(parts) > 5:
                    # 尝试解析为 Pose
                    is_pose = False
                    kp_parts = parts[5:]
                    has_vis = (len(kp_parts) % 3 == 0)
                    kp_count = len(kp_parts) // 3 if has_vis else len(kp_parts) // 2

                    # A YOLO pose line stores x, y, visibility triplets after
                    # the box.  It must not depend on the currently selected
                    # UI template: datasets can legitimately use a different
                    # number of keypoints.  A segmentation polygon can have a
                    # similar token count, so require every third value to be
                    # a valid integral visibility state (0, 1 or 2).
                    pose_visibility_values = []
                    if has_vis and kp_count > 0:
                        try:
                            pose_visibility_values = [float(kp_parts[index]) for index in range(2, len(kp_parts), 3)]
                        except ValueError:
                            pose_visibility_values = []
                    has_valid_pose_visibility = bool(pose_visibility_values) and all(
                        value in (0.0, 1.0, 2.0) for value in pose_visibility_values
                    )
                    
                    template = self.scene.current_pose_template
                    if template and len(template.get("keypoints", [])) == kp_count:
                        is_pose = True
                    elif has_valid_pose_visibility:
                        is_pose = True
                        
                    if is_pose:
                        cx, cy = float(parts[1]) * img_w, float(parts[2]) * img_h
                        w, h = float(parts[3]) * img_w, float(parts[4]) * img_h
                        if not template or len(template.get("keypoints", [])) != kp_count:
                            template = {"name": "YOLO_Import", "keypoints": [], "connections": []}
                            for i in range(kp_count):
                                template["keypoints"].append({"name": f"kp_{i}", "color": "#00FF00", "default_pos": [0.5, 0.5]})
                        
                        shape = PoseShape(QRectF(cx - w / 2, cy - h / 2, w, h), template, label)
                        idx = 0
                        for i in range(kp_count):
                            kx = float(kp_parts[idx]) * img_w
                            ky = float(kp_parts[idx+1]) * img_h
                            vis = int(float(kp_parts[idx+2])) if has_vis else 2
                            idx += 3 if has_vis else 2
                            if i < len(shape.kps):
                                local_pt = shape.mapFromScene(QPointF(kx, ky))
                                shape.kps[i].setPos(local_pt)
                                shape.kps[i].set_visibility(vis)
                        shape.update_lines()
                        loaded_shapes.append((shape, label))
                        continue

                    # 如果不是 Pose，且满足多边形条件
                    elif len(parts) % 2 == 1:
                        qpoints = [QPointF(float(parts[i]) * img_w, float(parts[i + 1]) * img_h) for i in
                                   range(1, len(parts), 2)]
                        shape = PolyShape(QPolygonF(qpoints), label)

                if shape is not None:
                    loaded_shapes.append((shape, label))
                else:
                    raise ValueError(f"无法解析 YOLO 标注行：{line.strip()[:120]}")
            for shape, label in loaded_shapes:
                self._add_shape_to_scene(shape, label)
        except Exception as e:
            self._record_annotation_load_error(txt_path, e)

    def _load_xml(self, xml_path):
        import xml.etree.ElementTree as ET
        if not os.path.exists(xml_path): return
        try:
            loaded_shapes = []
            tree = ET.parse(xml_path)
            root = tree.getroot()
            for obj in root.findall("object"):
                label = obj.find("name").text
                polyline = obj.find("polyline")
                bndbox = obj.find("bndbox")
                if polyline is not None:
                    points = []
                    for point in polyline.findall("pt"):
                        x = point.findtext("x")
                        y = point.findtext("y")
                        if x is not None and y is not None:
                            x, y = float(x), float(y)
                            if not math.isfinite(x) or not math.isfinite(y):
                                raise ValueError("XML 折线包含无效坐标")
                            points.append(QPointF(x, y))
                    if len(points) >= 2:
                        loaded_shapes.append((PolylineShape(QPolygonF(points), label), label))
                    else:
                        raise ValueError("XML 折线少于两个有效点")
                    continue
                if bndbox is not None:
                    xmin, ymin = float(bndbox.find("xmin").text), float(bndbox.find("ymin").text)
                    xmax, ymax = float(bndbox.find("xmax").text), float(bndbox.find("ymax").text)
                    if not all(math.isfinite(v) for v in (xmin, ymin, xmax, ymax)):
                        raise ValueError("XML 矩形包含无效坐标")
                    shape = RectShape(QRectF(xmin, ymin, xmax - xmin, ymax - ymin), label)
                    loaded_shapes.append((shape, label))
                else:
                    raise ValueError("XML 标注缺少支持的几何类型")
            for shape, label in loaded_shapes:
                self._add_shape_to_scene(shape, label)
        except Exception as e:
            self._record_annotation_load_error(xml_path, e)

    def _load_curvelanes(self, lines_path):
        """Load CondLaneNet's class-less .lines.txt sidecar as open polylines."""
        if not os.path.exists(lines_path):
            return
        # The upstream format deliberately has no class id.  Keep imported
        # lanes editable by assigning the first project class when available;
        # otherwise create a neutral lane class once.
        label = self.class_list[0] if self.class_list else "lane"
        try:
            loaded_shapes = []
            for points in Exporter.load_curvelanes(lines_path):
                qpoints = [QPointF(point[0], point[1]) for point in points]
                loaded_shapes.append((PolylineShape(QPolygonF(qpoints), label), label))
            for shape, shape_label in loaded_shapes:
                self._add_shape_to_scene(shape, shape_label)
        except Exception as e:
            self._record_annotation_load_error(lines_path, e)

    def save_annotation(self, format_type):
        if not self.current_image_path or not self.scene.img_item:
            QMessageBox.warning(self, "提示", "请先打开图片")
            DialogOver(self, "请先在左侧树形目录中打开图片", "操作错误", "warning")
            return
        try:
            shapes_data = Exporter.extract_shapes(self.scene)
            out_path = self.annotation_output_path(self.current_image_path, format_type)
            if format_type == "curvelanes" and shapes_data and not self._curvelanes_polyline_count(shapes_data):
                DialogOver(self, "当前没有可导出的折线。", "没有可导出的折线", "warning")
                return False
            signature = self._annotation_signature(shapes_data)
            if self._saved_annotation_signatures.get(out_path) == signature:
                self._check_annotation_target(out_path)
                archived = None
            else:
                archived = self._write_annotation_data(out_path, format_type, shapes_data)
            self._saved_annotation_signatures[out_path] = signature
            if format_type == self.current_format:
                self._autosave_dirty = False
                self._autosave_timer.stop()
                self._clear_save_failure()
            self.update_annotation_status()
            if not shapes_data:
                message = f"旧标注已移至回收站：\n{archived}" if archived else "当前界面没有标注信息。"
                DialogOver(self, message, "提示", "warning")
            elif format_type == "curvelanes" and len(shapes_data) != self._curvelanes_polyline_count(shapes_data):
                DialogOver(
                    self,
                    f"已导出 {self._curvelanes_polyline_count(shapes_data)} 条折线到 .lines.txt；其他标注未导出。",
                    "保存成功",
                    "success",
                )
            else:
                DialogOver(self, f"标注文件保存/更新成功！", "保存成功", "success")
            print(f"标注文件已保存到: {out_path}")
            return True
        except Exception as exc:
            if format_type == self.current_format:
                self._autosave_dirty = True
                self._set_save_failure(exc)
            DialogOver(self, f"写入失败，画布仍保留：{exc}", "保存出错", "danger")
            return False

    def closeEvent(self, event):
        if not self._ensure_pending_annotation_saved():
            recovery = self._last_recovery_snapshot
            try:
                matches = bool(recovery and recovery[0] == self.current_image_path
                               and recovery[1] == self._annotation_signature(Exporter.extract_shapes(self.scene))
                               and file_fingerprint(recovery[2]) == recovery[3])
            except Exception:
                matches = False
            if not matches or QMessageBox.question(
                self, "原位置未保存",
                f"当前画布已另存至：\n{recovery[2]}\n\n原标注位置仍未保存。确认关闭并保留恢复文件吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                event.ignore()
                return
            self._autosave_timer.stop()
            self._autosave_dirty = False
        self.sam_client.cleanup()

        # 记录上次的状态
        settings = QSettings("luohuabuxiema", "JotMark")
        if self.current_dir:
            settings.setValue("last_dir", self.current_dir)
        else:
            settings.setValue("last_dir", "")
        if self.current_image_path:
            settings.setValue("last_image", self.current_image_path)
        else:
            settings.setValue("last_image", "")

        super().closeEvent(event)

    def restore_last_state(self):
        settings = QSettings("luohuabuxiema", "JotMark")
        last_dir = settings.value("last_dir", "")
        last_image = settings.value("last_image", "")

        if last_dir and os.path.exists(last_dir):
            self.current_dir = last_dir
            self.load_classes(last_dir)
            self._rebuild_file_list(self._directory_image_paths(last_dir), last_image)

    def show_next_image(self):
        current_idx = self.listFiles.currentRow()
        if current_idx < self.listFiles.count() - 1:
            self.listFiles.setCurrentRow(current_idx + 1)

    def show_previous_image(self):
        current_idx = self.listFiles.currentRow()
        if current_idx > 0:
            self.listFiles.setCurrentRow(current_idx - 1)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key_Escape and self.sam_merge_selection_mode:
            self.cancel_region_merge_selection()
            event.accept()
            return

        # 撤销与重做快捷键（统一 Ctrl+Shift+Z 为重做）
        if key == Qt.Key_Z and modifiers == (Qt.ControlModifier | Qt.ShiftModifier):
            self.redo()
        elif key == Qt.Key_Z and modifiers == Qt.ControlModifier:
            self.undo()

        if key == Qt.Key_D or key == Qt.Key_Right:
            self.show_next_image()
        elif key == Qt.Key_A or key == Qt.Key_Left:
            self.show_previous_image()
        elif key == Qt.Key_Return or key == Qt.Key_Enter or key == Qt.Key_Escape:
            # 取消所有选中状态 (退出编辑模式)
            for item in self.scene.selectedItems():
                item.setSelected(False)
        elif key == Qt.Key_S and modifiers == Qt.ControlModifier:
            self.save_annotation(self.current_format)
        elif key == Qt.Key_E and self.scene.mode == CanvasMode.EDIT:  # 仅选择/编辑模式允许修改类别
            selected_items = self.scene.selectedItems()
            for item in selected_items:
                if hasattr(item, 'label'):
                    self.edit_shape_label(item)
                    break

        elif key == Qt.Key_Q:
            if not self.manual_only:
                self.samSwitch.setChecked(not self.samSwitch.isChecked())
        elif key == Qt.Key_F1:
            self.show_help_dialog()
        elif key == Qt.Key_R:
            self.actionRect.trigger()
        elif key == Qt.Key_V:
            self.actionSelect.trigger()
        elif key == Qt.Key_P:
            self.actionPoly.trigger()
        elif key == Qt.Key_L:
            self.actionLine.trigger()
        elif key == Qt.Key_M:
            # M for Model Prediction in Smart mode
            if self.samSwitch.isChecked() and getattr(self, 'current_yolo_predictor', None) is not None:
                self.btnPredict.click()
        elif key == Qt.Key_T:
            self.actionPoint.trigger()
        elif key == Qt.Key_O:
            self.actionRBox.trigger()

        super().keyPressEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    # window.show()
    sys.exit(app.exec())
