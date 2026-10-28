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


ATTRIBUTE_SCHEMA_FILE = "class_attributes.json"
ATTRIBUTE_TYPES = {"single_select", "multi_select", "text", "number", "boolean"}


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

    @property
    def path(self):
        if not self.directory:
            return None
        return os.path.join(self.directory, ATTRIBUTE_SCHEMA_FILE)

    def load(self, directory: str | None):
        self.directory = directory
        self._global = []
        self._classes = {}
        path = self.path
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as stream:
                raw = json.load(stream)
            classes = raw.get("classes", {}) if isinstance(raw, dict) else {}
            global_attributes = raw.get("global", []) if isinstance(raw, dict) else []
            used_ids = set()
            self._global = self._normalize_nodes(global_attributes, used_ids)
            if isinstance(classes, dict):
                self._classes = {
                    str(class_name): self._normalize_nodes(nodes, used_ids)
                    for class_name, nodes in classes.items()
                    if isinstance(nodes, list)
                }
        except (OSError, ValueError, TypeError):
            # A malformed optional schema must never prevent the dataset itself
            # from opening.  The next explicit save writes a valid revision.
            self._global = []
            self._classes = {}

    def save(self):
        path = self.path
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "version": 2,
            "global": self._global,
            "classes": self._classes,
        }
        fd, temporary_path = tempfile.mkstemp(
            prefix=".jotmark-attributes-", suffix=".tmp", dir=os.path.dirname(path), text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
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

            result.append({
                "id": node_id,
                "name": str(raw_node.get("name") or "新属性").strip() or "新属性",
                "type": node_type,
                "options": clean_options,
                "children": cls._normalize_nodes(raw_node.get("children", []), used_ids),
            })
        return result
