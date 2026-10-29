"""Safe class/attribute migration for JotMark JSON annotation projects.

All conversions are copy-on-write: the source directory is never modified;
its relevant annotation/configuration files are snapshotted first, then a new
destination directory is created with converted JSON annotations and metadata.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from jotmark.data.attribute_schema import new_attribute_node


CLASS_FILE = "classes.txt"
SCHEMA_FILE = "class_attributes.json"
REPORT_FILE = "category_conversion_report.json"
BACKUP_ROOT = ".jotmark_backups"
EXCLUDED_JSON = {SCHEMA_FILE, "class_colors.json"}


class ConversionError(ValueError):
    """Raised when a migration plan would be unsafe or ambiguous."""


def _read_json(path: Path, default):
    if not path.is_file():
        return deepcopy(default)
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError, TypeError) as exc:
        raise ConversionError(f"无法读取 {path.name}：{exc}") from exc


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def annotation_json_paths(source_dir: str | Path) -> list[Path]:
    source = Path(source_dir)
    return sorted(
        path for path in source.glob("*.json")
        if path.name not in EXCLUDED_JSON and path.is_file()
    )


def _read_classes(source: Path) -> list[str]:
    classes_path = source / CLASS_FILE
    if not classes_path.is_file():
        return []
    try:
        with classes_path.open("r", encoding="utf-8-sig") as stream:
            return list(dict.fromkeys(
                line.strip() for line in stream
                if line.strip() and not line.lstrip().startswith("#")
            ))
    except OSError as exc:
        raise ConversionError(f"无法读取 {CLASS_FILE}：{exc}") from exc


def _normalise_schema(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    global_nodes = raw.get("global", [])
    classes = raw.get("classes", {})
    return {
        "version": 2,
        "global": deepcopy(global_nodes) if isinstance(global_nodes, list) else [],
        "classes": deepcopy(classes) if isinstance(classes, dict) else {},
    }


def load_project_metadata(source_dir: str | Path) -> dict:
    """Read source class names, schema, labels and JSON annotation files."""
    source = Path(source_dir).expanduser().resolve()
    if not source.is_dir():
        raise ConversionError("源标注目录不存在。")
    files = annotation_json_paths(source)
    documents = {path: _read_json(path, {}) for path in files}
    class_names = _read_classes(source)
    for document in documents.values():
        for shape in document.get("shapes", []) if isinstance(document, dict) else []:
            label = str(shape.get("label", "")).strip() if isinstance(shape, dict) else ""
            if label and label not in class_names:
                class_names.append(label)
    schema = _normalise_schema(_read_json(source / SCHEMA_FILE, {}))
    return {
        "source": source,
        "files": files,
        "documents": documents,
        "classes": class_names,
        "schema": schema,
    }


def flatten_attribute_nodes(nodes, prefix="") -> list[dict]:
    """Return schema nodes with a display path, retaining their identifiers."""
    result = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        name = str(node.get("name") or "属性")
        path = f"{prefix} / {name}" if prefix else name
        result.append({"id": node["id"], "name": name, "path": path, "type": node.get("type", "single_select")})
        result.extend(flatten_attribute_nodes(node.get("children", []), path))
    return result


def attributes_for_class(schema: dict, class_name: str, include_global=True) -> list[dict]:
    nodes = []
    if include_global:
        nodes.extend(schema.get("global", []))
    nodes.extend(schema.get("classes", {}).get(class_name, []))
    return flatten_attribute_nodes(nodes)


def _find_node(nodes, node_id):
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict):
            if node.get("id") == node_id:
                return node
            found = _find_node(node.get("children", []), node_id)
            if found:
                return found
    return None


def _remove_node(nodes, node_id):
    for index, node in enumerate(list(nodes) if isinstance(nodes, list) else []):
        if not isinstance(node, dict):
            continue
        if node.get("id") == node_id:
            nodes.pop(index)
            return True
        if _remove_node(node.get("children", []), node_id):
            return True
    return False


def _node_ids(nodes) -> set[str]:
    ids = set()
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict):
            if node.get("id"):
                ids.add(node["id"])
            ids.update(_node_ids(node.get("children", [])))
    return ids


def _valid_attribute_ids(schema: dict, class_name: str) -> set[str]:
    return _node_ids(schema.get("global", [])) | _node_ids(schema.get("classes", {}).get(class_name, []))


def _scalar_key(value):
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def safe_category_suffix(value) -> str:
    value = _scalar_key(value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("_.-")
    return value.lower()


def discover_attribute_values(metadata: dict, source_class: str, attribute_id: str) -> dict[str, int]:
    values = {}
    for document in metadata["documents"].values():
        for shape in document.get("shapes", []) if isinstance(document, dict) else []:
            if not isinstance(shape, dict) or shape.get("label") != source_class:
                continue
            value = shape.get("attributes", {}).get(attribute_id) if isinstance(shape.get("attributes"), dict) else None
            key = _scalar_key(value)
            if key:
                values[key] = values.get(key, 0) + 1
    return values


def _backup_source(source: Path) -> Path:
    """Make a complete recoverable snapshot of the source project.

    A conversion may change class metadata as well as many JSON sidecars.  A
    partial backup is not enough to safely roll that back, especially when a
    project keeps images, templates, or auxiliary exports below the same
    directory.  Snapshot the whole source tree, while deliberately excluding
    the backup root itself so earlier snapshots never recurse into a new one.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = source / BACKUP_ROOT / f"category-conversion-{timestamp}"
    if backup.exists():
        # Extremely unlikely, but do not merge two conversions started in the
        # same second.  A suffix keeps the backup immutable and identifiable.
        backup = source / BACKUP_ROOT / (
            f"category-conversion-{timestamp}-{datetime.now().microsecond:06d}"
        )
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        backup,
        ignore=shutil.ignore_patterns(BACKUP_ROOT),
        copy_function=shutil.copy2,
    )
    return backup


def _validate_destination(source: Path, output_dir: str | Path) -> Path:
    output = Path(output_dir).expanduser().resolve()
    if output == source:
        raise ConversionError("输出目录必须与源标注目录不同。")
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise ConversionError("输出目录不能位于源标注目录内部。请选择源目录外的同级或其他位置。")
    try:
        source.relative_to(output)
    except ValueError:
        pass
    else:
        raise ConversionError("输出目录不能是源标注目录的父目录，避免覆盖源项目。")
    if output.exists():
        raise ConversionError("输出目录已存在。请选择一个尚未创建的新目录，避免覆盖已有文件。")
    return output


def _prepare_destination(source: Path, output_dir: str | Path) -> tuple[Path, Path]:
    output = _validate_destination(source, output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".jotmark-conversion-", dir=output.parent))
    return output, staging


def _write_classes(path: Path, class_names: list[str]):
    path.write_text("\n".join(class_names) + "\n", encoding="utf-8")


def _finish_conversion(output: Path, staging: Path, schema: dict, class_names: list[str], report: dict, backup: Path):
    try:
        _write_classes(staging / CLASS_FILE, class_names)
        _write_json(staging / SCHEMA_FILE, schema)
        _write_json(staging / REPORT_FILE, report | {"backup_directory": str(backup)})
        os.replace(staging, output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"output_directory": str(output), "backup_directory": str(backup), **report}


def _copy_source_to_staging(source: Path, staging: Path):
    """Create a standalone converted project without carrying old backups."""
    shutil.copytree(
        source,
        staging,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(BACKUP_ROOT),
        copy_function=shutil.copy2,
    )


def _attribute_node_and_scope(schema: dict, source_class: str, attribute_id: str):
    """Return an attribute node and whether it is global or class-local."""
    node = _find_node(schema.get("global", []), attribute_id)
    if node is not None:
        return node, "global"
    node = _find_node(schema.get("classes", {}).get(source_class, []), attribute_id)
    return node, "class" if node is not None else ""


def convert_category_to_category(source_dir, output_dir, source_class, target_class, keep_compatible_attributes=True, remove_source_category=True):
    metadata = load_project_metadata(source_dir)
    source = metadata["source"]
    source_class = str(source_class).strip()
    target_class = str(target_class).strip()
    if not source_class or not target_class:
        raise ConversionError("请选择源类别并填写目标类别。")
    if source_class == target_class:
        raise ConversionError("源类别与目标类别相同，无需转换。")
    if source_class not in metadata["classes"]:
        raise ConversionError("源类别不存在于该项目。")

    # Validate before making a snapshot, then snapshot before creating or
    # writing the converted project.  The source remains read-only throughout.
    _validate_destination(source, output_dir)
    backup = _backup_source(source)

    schema = deepcopy(metadata["schema"])
    if target_class not in schema["classes"] and source_class in schema["classes"]:
        schema["classes"][target_class] = deepcopy(schema["classes"][source_class])
    valid_ids = _valid_attribute_ids(schema, target_class)
    converted = 0
    for path, document in metadata["documents"].items():
        document = deepcopy(document)
        for shape in document.get("shapes", []) if isinstance(document, dict) else []:
            if not isinstance(shape, dict) or shape.get("label") != source_class:
                continue
            shape["label"] = target_class
            attrs = shape.get("attributes", {})
            if not keep_compatible_attributes or not isinstance(attrs, dict):
                shape["attributes"] = {}
            else:
                shape["attributes"] = {key: value for key, value in attrs.items() if key in valid_ids}
            converted += 1
        metadata["documents"][path] = document

    classes = list(metadata["classes"])
    if target_class not in classes:
        classes.append(target_class)
    if remove_source_category:
        classes = [name for name in classes if name != source_class]
        schema["classes"].pop(source_class, None)
    output, staging = _prepare_destination(source, output_dir)
    try:
        _copy_source_to_staging(source, staging)
        for path, document in metadata["documents"].items():
            _write_json(staging / path.name, document)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _finish_conversion(output, staging, schema, classes, {
        "mode": "category_to_category",
        "source_class": source_class,
        "target_class": target_class,
        "converted_shapes": converted,
        "keep_compatible_attributes": bool(keep_compatible_attributes),
    }, backup)


def convert_attribute_to_category(source_dir, output_dir, source_class, attribute_id, category_prefix, remove_lifted_attribute=False, remove_source_category=True):
    metadata = load_project_metadata(source_dir)
    source = metadata["source"]
    source_class = str(source_class).strip()
    prefix = str(category_prefix).strip()
    if not source_class or not attribute_id or not prefix:
        raise ConversionError("请选择源类别、具体属性并填写新类别前缀。")
    node, scope = _attribute_node_and_scope(metadata["schema"], source_class, attribute_id)
    if node is None:
        raise ConversionError("所选属性不存在于源类别或公共属性定义中。")
    if node.get("type") == "multi_select":
        raise ConversionError("多选属性不能直接提升为单一类别。请先拆分为单选属性后再转换。")
    if remove_lifted_attribute and scope == "global":
        raise ConversionError("公共属性同时作用于其他类别，不能在此转换中删除。请取消“移除已提升属性”。")
    values = discover_attribute_values(metadata, source_class, attribute_id)
    if not values:
        raise ConversionError("该类别没有已填写的可提升单值属性。多选属性不能直接提升为单一类别。")
    mapping = {value: f"{prefix}_{safe_category_suffix(value)}" for value in values}
    if any(not name.rstrip("_") for name in mapping.values()):
        raise ConversionError("属性值无法生成有效类别名，请先补全属性值。")

    _validate_destination(source, output_dir)
    backup = _backup_source(source)

    schema = deepcopy(metadata["schema"])
    source_schema = deepcopy(schema["classes"].get(source_class, []))
    if remove_lifted_attribute:
        _remove_node(source_schema, attribute_id)
    # A generated category may already exist in a mature dataset.  Its schema
    # is authoritative; never silently replace it with the source schema.
    # Fresh generated categories inherit the source schema instead.
    for new_class in mapping.values():
        if new_class not in schema["classes"]:
            schema["classes"][new_class] = deepcopy(source_schema)
    valid_ids_by_target = {
        new_class: _valid_attribute_ids(schema, new_class)
        for new_class in mapping.values()
    }
    converted = 0
    remaining_source_shapes = 0
    for path, document in metadata["documents"].items():
        document = deepcopy(document)
        for shape in document.get("shapes", []) if isinstance(document, dict) else []:
            if not isinstance(shape, dict) or shape.get("label") != source_class:
                continue
            attrs = shape.get("attributes", {})
            key = _scalar_key(attrs.get(attribute_id)) if isinstance(attrs, dict) else ""
            if not key:
                remaining_source_shapes += 1
                continue
            new_class = mapping[key]
            shape["label"] = new_class
            attrs = attrs if isinstance(attrs, dict) else {}
            if remove_lifted_attribute:
                attrs.pop(attribute_id, None)
            shape["attributes"] = {
                attr_id: value
                for attr_id, value in attrs.items()
                if attr_id in valid_ids_by_target[new_class]
            }
            converted += 1
        metadata["documents"][path] = document

    if remove_source_category and not remaining_source_shapes:
        schema["classes"].pop(source_class, None)

    classes = list(metadata["classes"])
    for new_class in mapping.values():
        if new_class not in classes:
            classes.append(new_class)
    if remove_source_category and not remaining_source_shapes:
        classes = [name for name in classes if name != source_class]

    output, staging = _prepare_destination(source, output_dir)
    try:
        _copy_source_to_staging(source, staging)
        for path, document in metadata["documents"].items():
            _write_json(staging / path.name, document)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _finish_conversion(output, staging, schema, classes, {
        "mode": "attribute_to_category",
        "source_class": source_class,
        "attribute_id": attribute_id,
        "category_prefix": prefix,
        "attribute_value_mapping": mapping,
        "converted_shapes": converted,
        "unconverted_source_shapes": remaining_source_shapes,
        "remove_lifted_attribute": bool(remove_lifted_attribute),
    }, backup)


def convert_category_to_attribute(source_dir, output_dir, source_class, target_class, attribute_id, attribute_name, attribute_value, remove_source_category=True):
    metadata = load_project_metadata(source_dir)
    source = metadata["source"]
    source_class = str(source_class).strip()
    target_class = str(target_class).strip()
    attribute_name = str(attribute_name).strip()
    attribute_value = str(attribute_value).strip()
    if not source_class or not target_class or not attribute_value:
        raise ConversionError("请选择源类别、填写目标类别和属性值。")
    if source_class == target_class:
        raise ConversionError("类别转属性需要不同的源类别与目标类别。")
    if source_class not in metadata["classes"]:
        raise ConversionError("源类别不存在于该项目。")

    _validate_destination(source, output_dir)
    backup = _backup_source(source)

    schema = deepcopy(metadata["schema"])
    target_nodes = schema["classes"].setdefault(target_class, [])
    node = _find_node(schema.get("global", []), attribute_id) if attribute_id else None
    if node is None and attribute_id:
        node = _find_node(target_nodes, attribute_id)
    if node is None:
        if not attribute_name:
            raise ConversionError("请选择目标属性，或填写新属性名称。")
        node = new_attribute_node(attribute_name)
        node["type"] = "single_select"
        node["options"] = [attribute_value]
        target_nodes.append(node)
    elif node.get("type") == "multi_select":
        raise ConversionError("多选属性不能用于“类别转属性”；请选择单选、文本、数值或是/否属性。")
    elif node.get("type") == "boolean":
        if attribute_value.lower() not in {"yes", "no", "true", "false", "是", "否"}:
            raise ConversionError("是/否属性的转换值只能为 是/否（或 yes/no）。")
        attribute_value = attribute_value.lower() in {"yes", "true", "是"}
    elif node.get("type") in {"single_select", "multi_select"}:
        options = node.setdefault("options", [])
        if attribute_value not in options:
            options.append(attribute_value)

    converted = 0
    valid_ids = _valid_attribute_ids(schema, target_class)
    for path, document in metadata["documents"].items():
        document = deepcopy(document)
        for shape in document.get("shapes", []) if isinstance(document, dict) else []:
            if not isinstance(shape, dict) or shape.get("label") != source_class:
                continue
            attrs = shape.get("attributes", {})
            attrs = attrs if isinstance(attrs, dict) else {}
            shape["label"] = target_class
            attrs = {key: value for key, value in attrs.items() if key in valid_ids}
            attrs[node["id"]] = attribute_value
            shape["attributes"] = attrs
            converted += 1
        metadata["documents"][path] = document

    classes = list(metadata["classes"])
    if target_class not in classes:
        classes.append(target_class)
    if remove_source_category:
        classes = [name for name in classes if name != source_class]
        schema["classes"].pop(source_class, None)
    output, staging = _prepare_destination(source, output_dir)
    try:
        _copy_source_to_staging(source, staging)
        for path, document in metadata["documents"].items():
            _write_json(staging / path.name, document)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _finish_conversion(output, staging, schema, classes, {
        "mode": "category_to_attribute",
        "source_class": source_class,
        "target_class": target_class,
        "attribute_id": node["id"],
        "attribute_name": node.get("name", attribute_name),
        "attribute_value": attribute_value,
        "converted_shapes": converted,
    }, backup)
