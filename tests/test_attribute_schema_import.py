"""Regression tests for validated attribute-schema replacement."""

import unittest

from jotmark.data.attribute_schema import ClassAttributeStore


class AttributeSchemaImportTests(unittest.TestCase):
    def test_replace_schema_keeps_valid_structure_and_unique_ids(self):
        store = ClassAttributeStore()
        result = store.replace_schema({
            "version": 2,
            "global": [{
                "id": "shared", "name": "visibility", "type": "single_select",
                "options": ["visible", "uncertain"], "children": [],
            }],
            "classes": {
                "boundary": [{
                    # A duplicate imported ID is regenerated safely.
                    "id": "shared", "name": "boundary_kind", "type": "single_select",
                    "options": ["road_edge", "curb"], "children": [],
                }],
            },
        })
        self.assertEqual(result, {"global_count": 1, "class_count": 1})
        self.assertEqual(store.global_attributes()[0]["id"], "shared")
        boundary = store.attributes_for("boundary")[0]
        self.assertNotEqual(boundary["id"], "shared")
        self.assertEqual(boundary["options"], ["road_edge", "curb"])

    def test_replace_schema_rejects_invalid_top_level_types(self):
        store = ClassAttributeStore()
        with self.assertRaisesRegex(ValueError, "global"):
            store.replace_schema({"global": {}, "classes": {}})
        with self.assertRaisesRegex(ValueError, "classes"):
            store.replace_schema({"global": [], "classes": []})


if __name__ == "__main__":
    unittest.main()
