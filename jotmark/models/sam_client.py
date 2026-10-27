# -*- coding: utf-8 -*-
import torch
import numpy as np
import cv2
import queue
from PySide6.QtCore import QObject, QThread, Signal
from PIL import Image

import os

try:
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor
except ImportError:
    pass

try:
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
except ImportError:
    pass

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True


# ============== SAM Model Map ==============
import sys

# 统一获取模型权重基准目录（自动适配打包 exe 根目录/源码目录，支持自动创建）
from jotmark.config.paths import get_weights_base_dir
from jotmark.config.runtime import device_label, inference_context, resolve_device
from jotmark.graphics.geometry import mask_to_annotation_geometry
MODEL_BASE_DIR = get_weights_base_dir()

def get_sam_model_map():
    sam_weights_dir = os.path.join(MODEL_BASE_DIR, "sam_weights")
    model_map = {}
    
    if not os.path.exists(sam_weights_dir):
        return model_map
        
    for f in os.listdir(sam_weights_dir):
        if not f.endswith(".pt"):
            continue
            
        path = os.path.join(sam_weights_dir, f)
        key = f.replace(".pt", "")
        size_mb = os.path.getsize(path) // (1024 * 1024)
        size_label = f"{size_mb} MB" if size_mb < 1024 else f"{size_mb/1024:.1f} GB"
        
        # Determine type and config based on filename
        if "sam3" in f.lower():
            model_map[key] = {
                "display_name": "SAM 3" if f.lower() == "sam3.pt" else f"SAM 3 ({key})",
                "type": "sam3",
                "weight": path,
                "config": None,
                "supports_text": True,
                "size_label": size_label,
            }
        else:
            # SAM 2 logic
            config = None
            f_lower = f.lower()
            if "tiny" in f_lower or "_t.pt" in f_lower:
                config = "configs/sam2.1/sam2.1_hiera_t.yaml"
            elif "small" in f_lower or "_s.pt" in f_lower:
                config = "configs/sam2.1/sam2.1_hiera_s.yaml"
            elif "base_plus" in f_lower or "_b+.pt" in f_lower or "base" in f_lower:
                config = "configs/sam2.1/sam2.1_hiera_b+.yaml"
            elif "large" in f_lower or "_l.pt" in f_lower:
                config = "configs/sam2.1/sam2.1_hiera_l.yaml"
                
            model_map[key] = {
                "display_name": key.replace("_", " ").title(),
                "type": "sam2",
                "weight": path,
                "config": config,
                "supports_text": False,
                "size_label": size_label,
            }
    return model_map

SAM_MODEL_MAP = get_sam_model_map()

# ============== SAM 3 Workers ==============
class Sam3ModelLoadWorker(QThread):
    loaded = Signal(object, object, bool, str)

    def __init__(self, checkpoint_path, device):
        super().__init__()
        self.checkpoint_path = checkpoint_path
        self.device = device

    def run(self):
        try:
            if self.device != "cuda":
                raise RuntimeError("官方 SAM3 当前需要 CUDA；请释放显存或在标注界面改用 SAM2 / YOLO 的 CPU 模式。")
            model = build_sam3_image_model(
                checkpoint_path=self.checkpoint_path,
                device=self.device,
                enable_inst_interactivity=True,
            )
            model.to(self.device)
            model.eval()
            processor = Sam3Processor(model, device=self.device)
            self.loaded.emit(model, processor, True, f"SAM 3 模型加载成功（{device_label(self.device)}）")
        except Exception as e:
            self.loaded.emit(None, None, False, str(e))


class Sam3InferenceWorker(QThread):
    result_ready = Signal(list, list, list, float, bool)
    text_result_ready = Signal(list, str)
    merge_result_ready = Signal(list, list, list, float)
    image_ready = Signal(int, bool, str)

    def __init__(self, device):
        super().__init__()
        self.device = device
        self.model = None
        self.processor = None
        self.inference_state = None
        self.task_queue = queue.Queue(maxsize=1)
        self.running = True
        self._latest_image_request_id = 0

    def run(self):
        while self.running:
            try:
                task_type, data, is_click = self.task_queue.get(timeout=0.05)

                if not self.model:
                    continue

                if task_type == 'set_image':
                    image_path, request_id = data
                    try:
                        with Image.open(image_path) as image:
                            pil_img = image.convert("RGB")
                        with torch.inference_mode(), inference_context(self.device):
                            state = self.processor.set_image(pil_img)
                        # A newer image was selected while this one was being
                        # encoded.  Discard the stale embedding rather than
                        # briefly enabling prompts for the wrong image.
                        if request_id != self._latest_image_request_id:
                            continue
                        self.inference_state = state
                        self.image_ready.emit(request_id, True, "")
                    except Exception as e:
                        if request_id == self._latest_image_request_id:
                            self.inference_state = None
                            self.image_ready.emit(request_id, False, str(e))
                    continue

                if not self.inference_state:
                    continue

                if task_type == 'point':
                    x, y = data
                    with torch.inference_mode(), inference_context(self.device):
                        masks, scores, _ = self.model.predict_inst(
                            inference_state=self.inference_state,
                            point_coords=np.array([[x, y]]),
                            point_labels=np.array([1]),
                            multimask_output=is_click
                        )

                    if len(scores) > 0:
                        best_idx = np.argmax(scores)
                        mask_np = masks[best_idx].cpu().numpy() if torch.is_tensor(masks) else masks[best_idx]
                        score_val = float(scores[best_idx].cpu() if torch.is_tensor(scores) else scores[best_idx])

                        poly_pts, rect_xywh, rect_obb = mask_to_annotation_geometry(mask_np, seed_point=(x, y))

                        self.result_ready.emit(poly_pts, rect_xywh, rect_obb, score_val, is_click)

                elif task_type == 'merge':
                    points = data
                    if len(points) < 2:
                        self.merge_result_ready.emit([], [], [], 0.0)
                        continue

                    with torch.inference_mode(), inference_context(self.device):
                        masks, scores, _ = self.model.predict_inst(
                            inference_state=self.inference_state,
                            point_coords=np.asarray(points, dtype=np.float32),
                            point_labels=np.ones(len(points), dtype=np.int32),
                            multimask_output=True,
                        )

                    if len(scores) == 0:
                        self.merge_result_ready.emit([], [], [], 0.0)
                        continue

                    best_idx = np.argmax(scores)
                    mask_np = masks[best_idx].cpu().numpy() if torch.is_tensor(masks) else masks[best_idx]
                    score_val = float(scores[best_idx].cpu() if torch.is_tensor(scores) else scores[best_idx])
                    poly_pts, rect_xywh, rect_obb = mask_to_annotation_geometry(mask_np, seed_point=points[0])
                    self.merge_result_ready.emit(poly_pts, rect_xywh, rect_obb, score_val)

                elif task_type == 'text':
                    prompts = data
                    if isinstance(prompts, str):
                        prompts = [prompts]
                    
                    if not self.processor:
                        continue

                    for prompt_text in prompts:
                        with torch.inference_mode(), inference_context(self.device):
                            out_state = self.processor.set_text_prompt(prompt=prompt_text, state=self.inference_state)

                            masks = out_state.get("masks", [])
                            scores = out_state.get("scores", [])
                            boxes = out_state.get("boxes", [])

                            results = []
                            if len(masks) > 0:
                                for i in range(len(masks)):
                                    mask_np = masks[i].cpu().numpy() if torch.is_tensor(masks[i]) else masks[i]
                                    mask_np = np.squeeze(mask_np)

                                    score_val = float(scores[i].cpu() if torch.is_tensor(scores[i]) else scores[i])
                                    box = boxes[i].cpu().numpy() if torch.is_tensor(boxes[i]) else boxes[i]

                                    if box.ndim > 1:
                                        box = box.squeeze()
                                    x1, y1, x2, y2 = box
                                    rect_xywh = [x1, y1, x2 - x1, y2 - y1]

                                    poly_pts, _, rect_obb = mask_to_annotation_geometry(mask_np)

                                    if poly_pts:
                                        results.append({
                                            "poly_pts": poly_pts,
                                            "rect": rect_xywh,
                                            "obb": rect_obb,
                                            "score": score_val
                                        })

                            self.text_result_ready.emit(results, prompt_text)

            except queue.Empty:
                continue
            except Exception as e:
                print(f"SAM3 推理错误: {e}")

    def request_inference(self, x, y, is_click=False):
        if not self.inference_state:
            return
        # Hover previews are expendable.  Never let one replace a queued click
        # prompt, which is the annotation the user explicitly requested.
        if not is_click and not self.task_queue.empty():
            return
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
            except queue.Empty:
                pass
        self.task_queue.put(('point', (x, y), is_click))

    def request_set_image(self, image_path, request_id):
        """Serialize image encoding with inference and retain only the newest image."""
        self._latest_image_request_id = request_id
        self.inference_state = None
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
            except queue.Empty:
                pass
        self.task_queue.put(('set_image', (image_path, request_id), False))

    def request_text_inference(self, prompt_text):
        if not self.inference_state:
            return
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
            except queue.Empty:
                pass
        self.task_queue.put(('text', prompt_text, True))

    def request_merge_inference(self, points):
        if not self.inference_state:
            return
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
            except queue.Empty:
                pass
        self.task_queue.put(('merge', points, True))

    def stop(self):
        self.running = False
        self.wait()


# ============== SAM 2.1 Workers ==============
class Sam2ModelLoadWorker(QThread):
    loaded = Signal(object, bool, str)

    def __init__(self, config_file, checkpoint_path, device):
        super().__init__()
        self.config_file = config_file
        self.checkpoint_path = checkpoint_path
        self.device = device

    def run(self):
        try:
            model = build_sam2(self.config_file, ckpt_path=self.checkpoint_path, device=self.device)
            predictor = SAM2ImagePredictor(model)
            self.loaded.emit(predictor, True, f"SAM 2.1 模型加载成功（{device_label(self.device)}）")
        except Exception as e:
            self.loaded.emit(None, False, str(e))


class Sam2InferenceWorker(QThread):
    """SAM 2.1 推理 Worker — 仅支持点击分割，不支持文本提示词"""
    result_ready = Signal(list, list, list, float, bool)
    image_ready = Signal(int, bool, str)

    def __init__(self, device):
        super().__init__()
        self.device = device
        self.predictor = None
        self.image_set = False
        self.task_queue = queue.Queue(maxsize=1)
        self.running = True
        self._latest_image_request_id = 0

    def run(self):
        while self.running:
            try:
                task_type, data, is_click = self.task_queue.get(timeout=0.05)

                if not self.predictor:
                    continue

                if task_type == 'set_image':
                    image_path, request_id = data
                    try:
                        with Image.open(image_path) as image:
                            img_np = np.array(image.convert("RGB"))
                        with torch.inference_mode(), inference_context(self.device):
                            self.predictor.set_image(img_np)
                        if request_id != self._latest_image_request_id:
                            continue
                        self.image_set = True
                        self.image_ready.emit(request_id, True, "")
                    except Exception as e:
                        if request_id == self._latest_image_request_id:
                            self.image_set = False
                            self.image_ready.emit(request_id, False, str(e))
                    continue

                if not self.image_set:
                    continue

                if task_type == 'point':
                    x, y = data
                    with torch.inference_mode(), inference_context(self.device):
                        masks, scores, _ = self.predictor.predict(
                            point_coords=np.array([[x, y]]),
                            point_labels=np.array([1]),
                            multimask_output=is_click,
                        )

                    if len(scores) > 0:
                        best_idx = np.argmax(scores)
                        mask_np = masks[best_idx]
                        score_val = float(scores[best_idx])

                        poly_pts, rect_xywh, rect_obb = mask_to_annotation_geometry(mask_np, seed_point=(x, y))

                        self.result_ready.emit(poly_pts, rect_xywh, rect_obb, score_val, is_click)

            except queue.Empty:
                continue
            except Exception as e:
                print(f"SAM2 推理错误: {e}")

    def request_inference(self, x, y, is_click=False):
        if not self.image_set:
            return
        if not is_click and not self.task_queue.empty():
            return
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
            except queue.Empty:
                pass
        self.task_queue.put(('point', (x, y), is_click))

    def request_set_image(self, image_path, request_id):
        self._latest_image_request_id = request_id
        self.image_set = False
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
            except queue.Empty:
                pass
        self.task_queue.put(('set_image', (image_path, request_id), False))

    def stop(self):
        self.running = False
        self.wait()


# ============== Unified SAM Client ==============
class SAMClient(QObject):
    model_status_changed = Signal(bool, str)
    inference_result = Signal(list, list, list, float, bool)
    text_result_ready = Signal(list, str)
    merge_result_ready = Signal(list, list, list, float)
    image_ready = Signal(int, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = None
        self.processor = None
        self.current_model_key = None   # 当前模型的 key
        self.current_model_type = None  # "sam3" / "sam2"
        self.device = resolve_device()

        # SAM3 worker
        self._sam3_worker = None
        # SAM2 worker
        self._sam2_worker = None
        # 当前活跃的推理 worker
        self._active_worker = None

        self.load_worker = None
        self._image_request_id = 0

    def load_model_by_key(self, model_key):
        """通过 key 从 SAM_MODEL_MAP 中选择并加载模型"""
        if model_key not in SAM_MODEL_MAP:
            self.model_status_changed.emit(False, f"未知模型: {model_key}")
            return
        
        # 如果已经是当前模型，不重复加载
        if model_key == self.current_model_key and self._active_worker is not None:
            self.model_status_changed.emit(True, f"{SAM_MODEL_MAP[model_key]['display_name']} 已就绪")
            return

        info = SAM_MODEL_MAP[model_key]
        model_type = info.get("type", "sam2")
        weight_path = info.get("weight")

        # 如果权重文件不存在，提示用户并不继续加载
        if not weight_path or not os.path.exists(weight_path):
            self.model_status_changed.emit(False, f"模型权重文件不存在或未下载")
            return

        # 先停止旧的推理 worker
        self._stop_active_worker()

        self.current_model_key = model_key
        self.current_model_type = model_type
        self.device = resolve_device()
        self.model_status_changed.emit(False, f"正在加载 {info.get('display_name', model_key)}（{device_label(self.device)}），请稍候...")

        if model_type == "sam3":
            self.load_worker = Sam3ModelLoadWorker(weight_path, self.device)
            self.load_worker.loaded.connect(self._on_sam3_loaded)
            self.load_worker.start()
        elif model_type == "sam2":
            config = info.get("config")
            self.load_worker = Sam2ModelLoadWorker(config, weight_path, self.device)
            self.load_worker.loaded.connect(self._on_sam2_loaded)
            self.load_worker.start()

    # 兼容旧接口
    def load_model_async(self, checkpoint_path):
        """向后兼容：直接加载 SAM3 模型"""
        self.current_model_key = "sam3"
        self.current_model_type = "sam3"
        self.device = resolve_device()
        self._stop_active_worker()
        self.model_status_changed.emit(False, "正在后台加载模型，请稍候...")
        self.load_worker = Sam3ModelLoadWorker(checkpoint_path, self.device)
        self.load_worker.loaded.connect(self._on_sam3_loaded)
        self.load_worker.start()

    def _stop_active_worker(self):
        """停止当前的推理 worker"""
        if self._sam3_worker:
            self._sam3_worker.stop()
            self._sam3_worker = None
        if self._sam2_worker:
            self._sam2_worker.stop()
            self._sam2_worker = None
        self._active_worker = None

    def _on_sam3_loaded(self, model, processor, success, msg):
        if success:
            self.model = model
            self.processor = processor
            # 启动 SAM3 推理线程
            self._sam3_worker = Sam3InferenceWorker(self.device)
            self._sam3_worker.model = model
            self._sam3_worker.processor = processor
            self._sam3_worker.result_ready.connect(self.inference_result)
            self._sam3_worker.text_result_ready.connect(self.text_result_ready)
            self._sam3_worker.merge_result_ready.connect(self.merge_result_ready)
            self._sam3_worker.image_ready.connect(self.image_ready.emit)
            self._sam3_worker.start()
            self._active_worker = self._sam3_worker
        self.model_status_changed.emit(success, msg)

    def _on_sam2_loaded(self, predictor, success, msg):
        if success:
            self.model = predictor
            self.processor = None  # SAM2 没有文本处理器
            # 启动 SAM2 推理线程
            self._sam2_worker = Sam2InferenceWorker(self.device)
            self._sam2_worker.predictor = predictor
            self._sam2_worker.result_ready.connect(self.inference_result)
            self._sam2_worker.image_ready.connect(self.image_ready.emit)
            self._sam2_worker.start()
            self._active_worker = self._sam2_worker
        self.model_status_changed.emit(success, msg)

    def set_image(self, image_path):
        """Encode the selected image in the SAM worker, never in the UI thread."""
        self._image_request_id += 1
        request_id = self._image_request_id
        if self.current_model_type == "sam3" and self._sam3_worker:
            self._sam3_worker.request_set_image(image_path, request_id)
            return request_id
        if self.current_model_type == "sam2" and self._sam2_worker:
            self._sam2_worker.request_set_image(image_path, request_id)
            return request_id
        return None

    def request_inference(self, x, y, is_click):
        if self._active_worker:
            self._active_worker.request_inference(x, y, is_click)

    def request_text_inference(self, prompt_text):
        """文本提示词推理（仅 SAM3 支持）"""
        if self.current_model_type == "sam3" and self._sam3_worker:
            import re
            prompts = [p.strip() for p in re.split(r'[,，、\s]+', prompt_text) if p.strip()]
            if prompts:
                self._sam3_worker.request_text_inference(prompts)

    def request_merge_inference(self, points):
        """Merge multiple selected regions with SAM3 positive-point prompts."""
        if self.current_model_type == "sam3" and self._sam3_worker:
            self._sam3_worker.request_merge_inference(points)

    def supports_text_prompt(self):
        """当前模型是否支持文本提示词"""
        if self.current_model_key and self.current_model_key in SAM_MODEL_MAP:
            return SAM_MODEL_MAP[self.current_model_key]["supports_text"]
        return False

    def cleanup(self):
        self._stop_active_worker()
        
        # 卸载模型以释放显存
        self.model = None
        self.processor = None
        self.predictor = None
        
        # 强制垃圾回收和显存清理
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
