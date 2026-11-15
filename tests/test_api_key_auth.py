import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api import auth


class ApiKeyAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_patch = patch.object(
            auth,
            "DB_PATH",
            Path(self.temp_dir.name) / "api_users.db",
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        auth.init_api_db()

    def test_valid_api_key_returns_user(self):
        api_key = auth.create_user({
            "id": 123456789,
            "username": "test_user",
            "first_name": "Test",
            "last_name": "User",
        })

        user = auth.get_user_by_key(api_key)

        self.assertIsNotNone(user)
        self.assertEqual(user["telegram_id"], 123456789)
        self.assertEqual(user["username"], "test_user")

    def test_banned_user_api_key_is_rejected(self):
        api_key = auth.create_user({
            "id": 987654321,
            "username": "banned_user",
        })
        auth.set_user_banned(987654321, True, "test")

        self.assertIsNone(auth.get_user_by_key(api_key))


if __name__ == "__main__":
    unittest.main()
