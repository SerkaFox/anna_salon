from django.test import SimpleTestCase

from clients.management.commands.import_treatwell_customers import (
    _repair_swapped_contact_fields,
)


class TreatwellContactRepairTests(SimpleTestCase):
    def test_swaps_phone_exported_as_name_with_name_exported_as_phone(self):
        self.assertEqual(
            _repair_swapped_contact_fields("691019909", "Eztizen"),
            ("Eztizen", "691019909"),
        )

    def test_moves_phone_out_of_name_when_exported_name_is_missing(self):
        self.assertEqual(
            _repair_swapped_contact_fields("654581713", ""),
            ("Cliente Treatwell", "654581713"),
        )

    def test_keeps_normal_contact_fields(self):
        self.assertEqual(
            _repair_swapped_contact_fields("Elena", "+34654581713"),
            ("Elena", "+34654581713"),
        )
