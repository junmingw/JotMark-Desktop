"""Regression tests for safe class/attribute conversion."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from jotmark.data.category_conversion import (
    ConversionError,
    convert_attribute_to_category,
    convert_category_to_attribute,
    convert_category_to_category,
)


def _write_json(path: Path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _make_project(root: Path, classes, schema, shapes):
    root.mkdir()
    (root / "classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")
    _write_json(root / "class_attributes.json", schema)
    _write_json(root / "sample.json", {"version": "5.0.0", "shapes": shapes})
    (root / "sample.jpg").write_bytes(b"not-a-real-image-but-must-be-backed-up")


class CategoryConversionTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temporary_directory.name)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_category_to_category_is_copy_on_write_and_has_full_backup(self):
        source = self.tmp_path / "source"
        schema = {
            "version": 2,
            "global": [{"id": "weather", "name": "Weather", "type": "single_select", "options": ["sun"], "children": []}],
            "classes": {
                "car": [{"id": "car_color", "name": "Color", "type": "single_select", "options": ["red"], "children": []}],
                "vehicle": [{"id": "vehicle_type", "name": "Type", "type": "single_select", "options": ["sedan"], "children": []}],
            },
        }
        original_shapes = [
            {"label": "car", "shape_type": "rectangle", "points": [], "attributes": {"weather": "sun", "car_color": "red"}},
            {"label": "person", "shape_type": "point", "points": [], "attributes": {}},
        ]
        _make_project(source, ["car", "vehicle", "person"], schema, original_shapes)
        original_document = (source / "sample.json").read_text(encoding="utf-8")

        result = convert_category_to_category(source, self.tmp_path / "converted", "car", "vehicle")

        self.assertEqual((source / "sample.json").read_text(encoding="utf-8"), original_document)
        self.assertTrue(Path(result["backup_directory"]).is_dir())
        self.assertEqual(
            (Path(result["backup_directory"]) / "sample.jpg").read_bytes(),
            b"not-a-real-image-but-must-be-backed-up",
        )
        self.assertTrue((Path(result["output_directory"]) / "sample.jpg").is_file())
        converted = _read_json(Path(result["output_directory"]) / "sample.json")
        converted_shape = converted["shapes"][0]
        self.assertEqual(converted_shape["label"], "vehicle")
        self.assertEqual(converted_shape["attributes"], {"weather": "sun"})
        self.assertNotIn(
            "car",
            (Path(result["output_directory"]) / "classes.txt").read_text(encoding="utf-8").splitlines(),
        )

    def test_attribute_to_category_and_partial_source_retention(self):
        source = self.tmp_path / "source"
        schema = {
            "version": 2,
            "global": [],
            "classes": {
                "boundary": [{"id": "boundary_kind", "name": "Kind", "type": "single_select", "options": ["curb", "median"], "children": []}],
            },
        }
        original_shapes = [
            {"label": "boundary", "shape_type": "linestrip", "points": [], "attributes": {"boundary_kind": "curb"}},
            {"label": "boundary", "shape_type": "linestrip", "points": [], "attributes": {"boundary_kind": "median"}},
            {"label": "boundary", "shape_type": "linestrip", "points": [], "attributes": {}},
        ]
        _make_project(source, ["boundary"], schema, original_shapes)

        result = convert_attribute_to_category(
            source, self.tmp_path / "converted", "boundary", "boundary_kind", "boundary",
            remove_lifted_attribute=True, remove_source_category=True,
        )

        result_dir = Path(result["output_directory"])
        labels = [shape["label"] for shape in _read_json(result_dir / "sample.json")["shapes"]]
        self.assertEqual(labels, ["boundary_curb", "boundary_median", "boundary"])
        self.assertEqual(result["unconverted_source_shapes"], 1)
        self.assertIn("boundary", (result_dir / "classes.txt").read_text(encoding="utf-8").splitlines())
        self.assertEqual(_read_json(source / "sample.json")["shapes"], original_shapes)

    def test_category_to_attribute_creates_schema_and_writes_value(self):
        source = self.tmp_path / "source"
        schema = {"version": 2, "global": [], "classes": {"boundary": []}}
        _make_project(source, ["boundary", "boundary_curb"], schema, [
            {"label": "boundary_curb", "shape_type": "linestrip", "points": [], "attributes": {}},
        ])

        result = convert_category_to_attribute(
            source, self.tmp_path / "converted", "boundary_curb", "boundary", "", "boundary_kind", "curb"
        )

        converted_shape = _read_json(Path(result["output_directory"]) / "sample.json")["shapes"][0]
        output_schema = _read_json(Path(result["output_directory"]) / "class_attributes.json")
        node = output_schema["classes"]["boundary"][0]
        self.assertEqual(converted_shape["label"], "boundary")
        self.assertEqual(converted_shape["attributes"], {node["id"]: "curb"})
        self.assertEqual(node["name"], "boundary_kind")
        self.assertEqual(node["options"], ["curb"])

    def test_attribute_to_existing_category_keeps_existing_schema(self):
        source = self.tmp_path / "source"
        schema = {
            "version": 2,
            "global": [],
            "classes": {
                "boundary": [{"id": "boundary_kind", "name": "Kind", "type": "single_select", "options": ["curb"], "children": []}],
                "boundary_curb": [{"id": "existing_note", "name": "Existing", "type": "text", "options": [], "children": []}],
            },
        }
        _make_project(source, ["boundary", "boundary_curb"], schema, [
            {"label": "boundary", "shape_type": "linestrip", "points": [], "attributes": {"boundary_kind": "curb"}},
        ])

        result = convert_attribute_to_category(
            source, self.tmp_path / "converted", "boundary", "boundary_kind", "boundary"
        )

        output = Path(result["output_directory"])
        output_schema = _read_json(output / "class_attributes.json")
        self.assertEqual(output_schema["classes"]["boundary_curb"][0]["id"], "existing_note")
        converted = _read_json(output / "sample.json")["shapes"][0]
        self.assertEqual(converted["attributes"], {})

    def test_rejects_existing_or_nested_output_and_multi_select_lift(self):
        source = self.tmp_path / "source"
        schema = {
            "version": 2,
            "global": [],
            "classes": {"road": [{"id": "tags", "name": "Tags", "type": "multi_select", "options": [], "children": []}]},
        }
        _make_project(source, ["road"], schema, [
            {"label": "road", "shape_type": "polygon", "points": [], "attributes": {"tags": ["a", "b"]}},
        ])

        with self.assertRaisesRegex(ConversionError, "源标注目录内部"):
            convert_category_to_category(source, source / "nested", "road", "pavement")
        with self.assertRaisesRegex(ConversionError, "多选属性"):
            convert_attribute_to_category(source, self.tmp_path / "converted", "road", "tags", "road")
        existing = self.tmp_path / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(ConversionError, "已存在"):
            convert_category_to_category(source, existing, "road", "pavement")


if __name__ == "__main__":
    unittest.main()
