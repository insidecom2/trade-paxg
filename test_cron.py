import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CRON_FILE = ROOT / "trade-paxg.cron"
CRON_LAUNCHER = ROOT / "run_cron_job.sh"
DOCKERFILE = ROOT / "Dockerfile"
LOCK_COMMAND = "/usr/bin/flock -n /tmp/trade-paxg-"


def scheduled_entries() -> list[str]:
    """Return executable entries from the /etc/cron.d file."""
    return [
        line
        for line in CRON_FILE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*(?:\d+|\*)", line)
    ]


class CronConfigurationTests(unittest.TestCase):
    def test_cron_container_uses_bangkok_timezone(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("TZ=Asia/Bangkok", dockerfile)
        self.assertIn("/usr/share/zoneinfo/$TZ /etc/localtime", dockerfile)
        self.assertNotIn("CRON_TZ=", CRON_FILE.read_text(encoding="utf-8"))

    def test_each_job_has_its_own_nonblocking_lock(self):
        entries = scheduled_entries()

        self.assertEqual(len(entries), 2)
        for entry in entries:
            with self.subTest(entry=entry):
                self.assertIn(LOCK_COMMAND, entry)

    def test_4h_strategy_job_is_scheduled(self):
        entries = scheduled_entries()

        strategy_entries = [entry for entry in entries if "run_cron_job.sh 4h" in entry]

        self.assertEqual(len(strategy_entries), 1, "expected one 4h strategy cron entry")
        self.assertTrue(strategy_entries[0].startswith("1 0,4,8,12,16,20 * * 1-5"))

    def test_exit_profit_job_is_disabled(self):
        entries = scheduled_entries()

        self.assertFalse(
            any("run_exit_profit_job.sh" in entry for entry in entries),
            "exit-profit monitor must not have an active cron entry",
        )

    def test_liquidity_sweep_job_is_scheduled_within_bangkok_window(self):
        entries = scheduled_entries()
        sweep_entries = [e for e in entries if "run_liquidity_sweep_job.sh" in e]

        self.assertEqual(len(sweep_entries), 1)
        for entry in sweep_entries:
            with self.subTest(entry=entry):
                self.assertIn("/tmp/trade-paxg-liquidity-sweep.lock", entry)
                self.assertIn("1-5", entry)
        self.assertTrue(any("0 12-20" in entry for entry in sweep_entries))

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

    def test_liquidity_sweep_launcher_runs_the_watch(self):
        launcher = (ROOT / "run_liquidity_sweep_job.sh").read_text(encoding="utf-8")

        self.assertIn("liquidity_sweep.py", launcher)
        self.assertIn('--symbol "${TRADING_SYMBOL:-PAXG/USDT}"', launcher)


if __name__ == "__main__":
    unittest.main()
