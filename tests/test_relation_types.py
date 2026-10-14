from __future__ import annotations

import unittest

from app.workflows.ingest.relation_types import (
    RelationDefinition,
    is_more_specific_relation,
    validate_relation_definitions,
)


class RelationTypeHierarchyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definitions = [
            RelationDefinition(
                "related_to",
                "related_to",
                "Generic relation.",
                is_symmetric=True,
            ),
            RelationDefinition(
                "knows",
                "knows",
                "People know each other.",
                is_symmetric=True,
                parent_name="related_to",
            ),
            RelationDefinition(
                "family_of",
                "family_of",
                "People are family.",
                is_symmetric=True,
                parent_name="knows",
            ),
            RelationDefinition(
                "parent_of",
                "child_of",
                "Subject is a parent of object.",
                parent_name="family_of",
            ),
        ]

    def test_detects_direct_and_transitive_specialization(self) -> None:
        self.assertTrue(
            is_more_specific_relation(
                proposed_name="parent_of",
                existing_name="family_of",
                definitions=self.definitions,
            )
        )
        self.assertTrue(
            is_more_specific_relation(
                proposed_name="parent_of",
                existing_name="related_to",
                definitions=self.definitions,
            )
        )

    def test_does_not_treat_parent_sibling_or_same_type_as_more_specific(self) -> None:
        for proposed_name, existing_name in (
            ("family_of", "parent_of"),
            ("knows", "family_of"),
            ("family_of", "family_of"),
        ):
            self.assertFalse(
                is_more_specific_relation(
                    proposed_name=proposed_name,
                    existing_name=existing_name,
                    definitions=self.definitions,
                )
            )

    def test_validation_rejects_missing_parent(self) -> None:
        definitions = [
            RelationDefinition(
                "related_to",
                "related_to",
                "Generic relation.",
                is_symmetric=True,
                parent_name="missing",
            )
        ]

        with self.assertRaisesRegex(ValueError, "is not in the active catalog"):
            validate_relation_definitions(definitions)

    def test_validation_rejects_parent_cycle(self) -> None:
        definitions = [
            RelationDefinition(
                "first",
                "first",
                "First relation.",
                is_symmetric=True,
                parent_name="second",
            ),
            RelationDefinition(
                "second",
                "second",
                "Second relation.",
                is_symmetric=True,
                parent_name="first",
            ),
        ]

        with self.assertRaisesRegex(ValueError, "contains a cycle"):
            validate_relation_definitions(definitions)


if __name__ == "__main__":
    unittest.main()
