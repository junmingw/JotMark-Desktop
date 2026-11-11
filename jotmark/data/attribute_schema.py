"""Class-scoped attribute schemas for JotMark annotations.

The schema belongs to a class, while the selected values live on each shape.
Keeping the two stores separate lets a dataset evolve its attribute definitions
without duplicating them into every annotation file.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
import tempfile
import uuid
from jotmark.data.annotation_safety import backup_previous_annotation, file_fingerprint


ATTRIBUTE_SCHEMA_FILE = "class_attributes.json"
ATTRIBUTE_TYPES = {"single_select", "multi_select", "text", "number", "boolean"}


def read_attribute_json(path):
    """Read a JSON schema from common Windows and cross-platform encodings."""
    with open(path, "rb") as stream:
        content = stream.read()
    if content.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        text = content.decode("utf-32")
    elif content.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = content.decode("utf-16")
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030")
    return json.loads(text, parse_constant=_reject_json_constant)


def _reject_json_constant(value):
    raise ValueError(f"属性文件包含无效数值：{value}")


def _validate_schema_tree(nodes, seen_ids, depth=0, strict_ids=True):
    if depth > 100:
        raise ValueError("属性层级超过 100 层，无法安全处理")
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("children", []), list):
            raise ValueError("属性节点或其 children 格式无效")
        if node.get("type", "single_select") not in ATTRIBUTE_TYPES:
            raise ValueError("属性节点包含不支持的类型")
        if not isinstance(node.get("options", []), (list, str)):
            raise ValueError("属性选项格式无效")
        node_id = str(node.get("id") or "").strip()
        if node_id:
            if strict_ids and node_id in seen_ids:
                raise ValueError(f"属性 ID 重复：{node_id}")
            seen_ids.add(node_id)
        _validate_schema_tree(node.get("children", []), seen_ids, depth + 1, strict_ids)


def new_attribute_id() -> str:
    return uuid.uuid4().hex


def new_attribute_node(name: str = "新属性") -> dict:
    return {
        "id": new_attribute_id(),
        "name": str(name).strip() or "新属性",
        "type": "single_select",
        "options": [],
        "children": [],
    }


class ClassAttributeStore:
    """Load, validate and persist per-class attribute definition trees."""

    def __init__(self):
        self.directory = None
        self._global = []
        self._classes = {}
        self._document_metadata = {}
        self.load_error = None
        self._disk_fingerprint = None
        self._fingerprint_known = False

    @property
    def path(self):
        if not self.directory:
            return None
        return os.path.join(self.directory, ATTRIBUTE_SCHEMA_FILE)

    def load(self, directory: str | None):
        self.directory = directory
        self._global = []
        self._classes = {}
        self._document_metadata = {}
        self.load_error = None
        self._disk_fingerprint = None
        self._fingerprint_known = False
        path = self.path
        if not path:
            return
        try:
            self._disk_fingerprint = file_fingerprint(path)
            self._fingerprint_known = True
            if self._disk_fingerprint is None:
                return
            raw = read_attribute_json(path)
            if file_fingerprint(path) != self._disk_fingerprint:
                raise ValueError("读取期间属性文件发生变化")
            if not isinstance(raw, dict) or not isinstance(raw.get("global", []), list) or not isinstance(raw.get("classes", {}), dict):
                raise ValueError("属性文件必须包含有效的 global 数组和 classes 对象")
            classes = raw.get("classes", {}) if isinstance(raw, dict) else {}
            global_attributes = raw.get("global", []) if isinstance(raw, dict) else []
            if any(not isinstance(nodes, list) for nodes in classes.values()):
                raise ValueError("类别属性必须是数组")
            seen_ids = set()
            _validate_schema_tree(global_attributes, seen_ids)
            for nodes in classes.values():
                _validate_schema_tree(nodes, seen_ids)
            self._document_metadata = {key: deepcopy(value) for key, value in raw.items()
                                       if key not in {"global", "classes"}}
            used_ids = set()
            self._global = self._normalize_nodes(global_attributes, used_ids)
            if isinstance(classes, dict):
                self._classes = {
                    str(class_name): self._normalize_nodes(nodes, used_ids)
                    for class_name, nodes in classes.items()
                    if isinstance(nodes, list)
                }
        except (OSError, ValueError, TypeError) as exc:
            # Open the dataset without replacing or silently normalizing an
            # unreadable schema; explicit import may replace it after backup.
            self._global = []
            self._classes = {}
            self._document_metadata = {}
            self.load_error = str(exc)

    def replace_schema(self, raw) -> dict:
        """Validate and replace schema definitions while retaining stable IDs.

        This is used by the manual JSON import action.  It only changes the
        definitions held by this store; existing annotation instance values
        are deliberately not deleted here, so importing a new schema cannot
        silently destroy historical JSON metadata.
        """
        if not isinstance(raw, dict):
            raise ValueError("属性文件根节点必须是 JSON 对象。")
        global_attributes = raw.get("global", [])
        classes = raw.get("classes", {})
        if not isinstance(global_attributes, list):
            raise ValueError("属性文件中的 global 必须是数组。")
        if not isinstance(classes, dict):
            raise ValueError("属性文件中的 classes 必须是对象。")

        seen_ids = set()
        _validate_schema_tree(global_attributes, seen_ids, strict_ids=False)
        for nodes in classes.values():
            if not isinstance(nodes, list):
                raise ValueError("类别属性必须是数组。")
            _validate_schema_tree(nodes, seen_ids, strict_ids=False)

        used_ids = set()
        normalized_global = self._normalize_nodes(global_attributes, used_ids)
        normalized_classes = {}
        for class_name, nodes in classes.items():
            if not isinstance(nodes, list):
                raise ValueError(f"类别“{class_name}”的属性必须是数组。")
            clean_name = str(class_name).strip()
            if clean_name:
                normalized_classes[clean_name] = self._normalize_nodes(nodes, used_ids)

        self._global = normalized_global
        self._classes = normalized_classes
        self._document_metadata = {key: deepcopy(value) for key, value in raw.items()
                                   if key not in {"global", "classes"}}
        self.load_error = None
        return {
            "global_count": len(self._global),
            "class_count": len(self._classes),
        }

    def matches_import(self, raw) -> bool:
        """Compare effective definitions without generating new IDs for omitted IDs.

        Explicit IDs, extra metadata, nesting, option order and class names all
        remain significant. Missing IDs may reuse the existing positional ID,
        because retaining it protects already-saved annotation values.
        """
        if self.load_error:
            return False
        incoming = deepcopy(raw)

        def reuse_missing_ids(nodes, existing):
            if not isinstance(nodes, list):
                return
            for index, node in enumerate(nodes):
                if not isinstance(node, dict):
                    continue
                old = existing[index] if index < len(existing) else {}
                if not node.get("id") and old.get("id"):
                    node["id"] = old["id"]
                reuse_missing_ids(node.get("children", []), old.get("children", []))

        reuse_missing_ids(incoming.get("global", []), self._global)
        for name, nodes in incoming.get("classes", {}).items():
            reuse_missing_ids(nodes, self._classes.get(str(name).strip(), []))
        candidate = ClassAttributeStore()
        candidate.replace_schema(incoming)

        def document(store):
            metadata = deepcopy(store._document_metadata)
            metadata.setdefault("version", 2)
            metadata.update({"global": store._global, "classes": store._classes})
            return metadata

        # Python considers True == 1 and 1 == 1.0; JSON metadata can attach
        # meaning to these distinct types, so compare canonical JSON instead.
        def canonical(value):
            return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

        return canonical(document(candidate)) == canonical(document(self))

    def save(self):
        path = self.path
        if not path:
            return
        if self.load_error:
            raise RuntimeError(f"属性文件读取失败，禁止覆盖原文件：{self.load_error}")
        current_fingerprint = file_fingerprint(path)
        if not self._fingerprint_known:
            if current_fingerprint is not None:
                raise RuntimeError("属性文件尚未读取，禁止覆盖")
            self._fingerprint_known = True
            self._disk_fingerprint = None
        elif current_fingerprint != self._disk_fingerprint:
            raise RuntimeError("属性文件已被其他程序修改，禁止覆盖")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = deepcopy(self._document_metadata)
        data.setdefault("version", 2)
        data.update({"global": self._global, "classes": self._classes})
        fd, temporary_path = tempfile.mkstemp(
            prefix=".jotmark-attributes-", suffix=".tmp", dir=os.path.dirname(path), text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            backup_previous_annotation(path)
            if file_fingerprint(path) != self._disk_fingerprint:
                raise RuntimeError("备份期间属性文件被其他程序修改，禁止覆盖")
            os.replace(temporary_path, path)
            self._disk_fingerprint = file_fingerprint(path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

    def attributes_for(self, class_name: str) -> list[dict]:
        return deepcopy(self._classes.get(class_name, []))

    def global_attributes(self) -> list[dict]:
        return deepcopy(self._global)

    def set_global_attributes(self, nodes: list[dict]):
        used_ids = set()
        for class_nodes in self._classes.values():
            used_ids.update(self._collect_ids(class_nodes))
        self._global = self._normalize_nodes(nodes, used_ids)

    def set_attributes(self, class_name: str, nodes: list[dict]):
        if not class_name:
            return
        used_ids = self._collect_ids(self._global)
        for other_class, class_nodes in self._classes.items():
            if other_class != class_name:
                used_ids.update(self._collect_ids(class_nodes))
        normalized = self._normalize_nodes(nodes, used_ids)
        if normalized:
            self._classes[class_name] = normalized
        else:
            self._classes.pop(class_name, None)

    def attribute_ids_for(self, class_name: str = "", include_global: bool = True) -> set[str]:
        """Return every schema node ID, including nested child attributes."""
        ids = set()

        def collect(nodes):
            for node in nodes:
                node_id = node.get("id")
                if node_id:
                    ids.add(node_id)
                collect(node.get("children", []))

        if include_global:
            collect(self._global)
        collect(self._classes.get(class_name, []))
        return ids

    def all_class_attribute_ids(self) -> set[str]:
        """Return all known category-specific IDs, including nested nodes."""
        ids = set()
        for nodes in self._classes.values():
            ids.update(self._collect_ids(nodes))
        return ids

    def verify_disk_revision(self):
        """Reject use of an unreadable or externally changed project schema."""
        if self.load_error:
            raise RuntimeError(f"属性文件读取失败：{self.load_error}")
        if not self.path:
            return
        actual = file_fingerprint(self.path)
        if ((self._fingerprint_known and actual != self._disk_fingerprint)
                or (not self._fingerprint_known and actual is not None)):
            raise RuntimeError("属性文件已被其他程序修改或尚未读取，请重新打开项目。")

    @staticmethod
    def _collect_ids(nodes) -> set[str]:
        ids = set()
        for node in nodes:
            node_id = node.get("id")
            if node_id:
                ids.add(node_id)
            ids.update(ClassAttributeStore._collect_ids(node.get("children", [])))
        return ids

    def rename_class(self, old_name: str, new_name: str):
        if old_name == new_name:
            return
        if old_name in self._classes:
            self._classes[new_name] = self._classes.pop(old_name)

    def remove_class(self, class_name: str):
        """Delete the attribute definition tree owned by one class."""
        self._classes.pop(class_name, None)

    @classmethod
    def _normalize_nodes(cls, nodes, used_ids=None) -> list[dict]:
        used_ids = set() if used_ids is None else used_ids
        result = []
        for raw_node in nodes if isinstance(nodes, list) else []:
            if not isinstance(raw_node, dict):
                continue
            node_id = str(raw_node.get("id") or "").strip()
            if not node_id or node_id in used_ids:
                node_id = new_attribute_id()
            used_ids.add(node_id)

            node_type = str(raw_node.get("type") or "single_select")
            if node_type not in ATTRIBUTE_TYPES:
                node_type = "single_select"

            options = raw_node.get("options", [])
            if isinstance(options, str):
                options = options.splitlines()
            clean_options = []
            for option in options if isinstance(options, list) else []:
                option = str(option).strip()
                if option and option not in clean_options:
                    clean_options.append(option)

            node = deepcopy(raw_node)
            node.update({
                "id": node_id,
                "name": str(raw_node.get("name") or "新属性").strip() or "新属性",
                "type": node_type,
                "options": clean_options,
                "children": cls._normalize_nodes(raw_node.get("children", []), used_ids),
            })
            result.append(node)
        return result
