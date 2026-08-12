import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CRON_FILE = ROOT / "trade-paxg.cron"
CRON_LAUNCHER = ROOT / "run_cron_job.sh"
LOCK_COMMAND = "/usr/bin/flock -n /tmp/trade-paxg-"


def scheduled_entries() -> list[str]:
    """Return executable entries from the /etc/cron.d file."""
    return [
        line
        for line in CRON_FILE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*(?:\d+|\*)", line)
    ]


class CronConfigurationTests(unittest.TestCase):
    def test_each_timeframe_has_its_own_nonblocking_lock(self):
        entries = scheduled_entries()

        self.assertEqual(len(entries), 2)
        self.assertIn(" 1h ", entries[0])
        self.assertIn(" 4h ", entries[1])
        self.assertNotEqual(
            re.search(r"flock -n (\S+)", entries[0]).group(1),
            re.search(r"flock -n (\S+)", entries[1]).group(1),
        )
        for entry in entries:
            with self.subTest(entry=entry):
                self.assertIn(LOCK_COMMAND, entry)

    def test_launcher_forwards_an_explicit_timeframe(self):
        launcher = CRON_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn(
            'export TRADING_TIMEFRAME="${1:-${TRADING_TIMEFRAME:-4h}}"',
            launcher,
        )
        self.assertIn('--timeframe "${TRADING_TIMEFRAME:-4h}"', launcher)


if __name__ == "__main__":
    unittest.main()
