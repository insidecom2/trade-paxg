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
    def test_each_job_has_its_own_nonblocking_lock(self):
        entries = scheduled_entries()

        self.assertEqual(len(entries), 2)
        for entry in entries:
            with self.subTest(entry=entry):
                self.assertIn(LOCK_COMMAND, entry)

    def test_4h_strategy_job_is_scheduled(self):
        entries = scheduled_entries()

        self.assertTrue(
            any("run_cron_job.sh 4h" in entry for entry in entries),
            "expected a 4h strategy cron entry",
        )

    def test_hourly_exit_profit_job_is_scheduled(self):
        entries = scheduled_entries()

        self.assertTrue(
            any(
                "run_exit_profit_job.sh" in entry and "/tmp/trade-paxg-exit.lock" in entry
                for entry in entries
            ),
            "expected an hourly exit-profit cron entry",
        )

    def test_launcher_forwards_an_explicit_timeframe(self):
        launcher = CRON_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn(
            'export TRADING_TIMEFRAME="${1:-${TRADING_TIMEFRAME:-4h}}"',
            launcher,
        )
        self.assertIn('--timeframe "${TRADING_TIMEFRAME:-4h}"', launcher)

    def test_exit_profit_launcher_runs_the_standalone_check(self):
        launcher = (ROOT / "run_exit_profit_job.sh").read_text(encoding="utf-8")

        self.assertIn("exit_profit.py", launcher)
        self.assertIn('--symbol "${TRADING_SYMBOL:-PAXG/USDT}"', launcher)


if __name__ == "__main__":
    unittest.main()
