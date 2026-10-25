from __future__ import annotations

import unittest

from app.workflows.ingest.relation_types import (
    RelationDefinition,
    is_more_specific_relation,
    relation_catalog_prompt,
    repair_relation_endpoints,
    valid_relation_labels,
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


ENDPOINT_DEFINITIONS = [
    RelationDefinition("related_to", "related_to", "Fallback.", is_symmetric=True),
    RelationDefinition(
        "occurs_on",
        "occurrence_date_of",
        "Event date.",
        allowed_from_types=("event",),
        allowed_to_types=("date",),
    ),
    RelationDefinition(
        "scheduled_for",
        "scheduled_date_of",
        "Task date.",
        allowed_from_types=("task",),
        allowed_to_types=("date",),
    ),
    RelationDefinition(
        "lives_in",
        "residence_of",
        "Home.",
        allowed_from_types=("person",),
        allowed_to_types=("location",),
    ),
    RelationDefinition(
        "visited",
        "visited_by",
        "Visit.",
        allowed_from_types=("person",),
        allowed_to_types=("location",),
    ),
    RelationDefinition(
        "knows",
        "knows",
        "Acquaintance.",
        is_symmetric=True,
        allowed_from_types=("person",),
        allowed_to_types=("person",),
    ),
]


class RelationEndpointTests(unittest.TestCase):
    def definition(self, name: str) -> RelationDefinition:
        return next(item for item in ENDPOINT_DEFINITIONS if item.name == name)

    def test_catalog_prompt_shows_endpoint_types_for_both_directions(self) -> None:
        prompt = relation_catalog_prompt(ENDPOINT_DEFINITIONS)

        self.assertIn(
            "- occurs_on (event -> date) / occurrence_date_of (date -> event):",
            prompt,
        )
        self.assertIn("- knows (person <-> person):", prompt)
        self.assertIn("- related_to (any <-> any):", prompt)

    def test_valid_labels_include_inverse_direction(self) -> None:
        self.assertEqual(
            valid_relation_labels(ENDPOINT_DEFINITIONS, "date", "event"),
            ("related_to", "occurrence_date_of"),
        )

    def test_repair_reverses_a_relation_that_fits_backwards(self) -> None:
        repaired = repair_relation_endpoints(
            self.definition("occurs_on"), ENDPOINT_DEFINITIONS, "date", "event"
        )

        self.assertEqual(repaired, (self.definition("occurs_on"), True))

    def test_repair_picks_the_single_restricted_fit(self) -> None:
        repaired = repair_relation_endpoints(
            self.definition("scheduled_for"), ENDPOINT_DEFINITIONS, "event", "date"
        )

        self.assertEqual(repaired, (self.definition("occurs_on"), False))

    def test_repair_declines_when_several_relations_fit(self) -> None:
        self.assertIsNone(
            repair_relation_endpoints(
                self.definition("knows"), ENDPOINT_DEFINITIONS, "person", "location"
            )
        )


if __name__ == "__main__":
    unittest.main()
