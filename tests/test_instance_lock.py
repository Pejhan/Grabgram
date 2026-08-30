from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tg_downloader"))

from instance_lock import InstanceLock


class InstanceLockTests(unittest.TestCase):
    def test_second_instance_is_rejected_until_first_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "app.lock"
            first = InstanceLock(path)
            try:
                with self.assertRaises(RuntimeError):
                    InstanceLock(path)
            finally:
                first.close()
            reopened = InstanceLock(path)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
