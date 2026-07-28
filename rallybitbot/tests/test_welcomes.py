from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commands import welcomes


class WelcomeDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        welcomes.delivery_guard.clear()
        welcomes._last_marker_cleanup = 0.0

    def test_duplicate_join_and_leave_events_are_claimed_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_file = str(Path(directory) / "welcome_settings.json")
            with patch.object(welcomes, "WELCOME_SETTINGS_FILE", settings_file):
                self.assertTrue(welcomes._claim_delivery("join", 1, 2))
                self.assertFalse(welcomes._claim_delivery("join", 1, 2))
                self.assertTrue(welcomes._claim_delivery("leave", 1, 2))
                welcomes.delivery_guard.clear()
                self.assertFalse(welcomes._claim_delivery("join", 1, 2))


if __name__ == "__main__":
    unittest.main()
