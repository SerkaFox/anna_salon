import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User
from clients.models import Client


class RepairSwappedClientContactsTests(TestCase):
    def test_repairs_only_clear_swaps_and_keeps_backup(self):
        user = User.objects.create_user(username="maria", password="secret")
        swapped = Client.objects.create(
            user=user,
            first_name="+34 600 111 222",
            phone="María López",
        )
        valid = Client.objects.create(
            first_name="Ana",
            phone="+34 600 333 444",
        )

        with TemporaryDirectory() as directory:
            backup_path = Path(directory) / "contacts.json"
            call_command(
                "repair_swapped_client_contacts",
                "--apply",
                backup=backup_path,
            )
            backup = json.loads(backup_path.read_text(encoding="utf-8"))

        swapped.refresh_from_db()
        valid.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(swapped.first_name, "María López")
        self.assertEqual(swapped.phone, "+34 600 111 222")
        self.assertEqual(user.first_name, "María López")
        self.assertEqual(user.phone, "+34 600 111 222")
        self.assertEqual(valid.first_name, "Ana")
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0]["id"], swapped.pk)
