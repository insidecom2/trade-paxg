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

        # Active today: MySQL price alert, DAILY_OUTLOOK, SETUP_DETECTION,
        # SETUP_CONFIRMATION, FINAL_SESSION_DECISION. Strategy, exit-profit,
        # liquidity-sweep, and SESSION_PREPARATION remain disabled.
        self.assertEqual(len(entries), 5)
        for entry in entries:
            with self.subTest(entry=entry):
                self.assertIn(LOCK_COMMAND, entry)

    def test_4h_strategy_job_is_disabled(self):
        entries = scheduled_entries()

        self.assertFalse(
            any("run_cron_job.sh 4h" in entry for entry in entries),
            "4h strategy notifications must not have an active cron entry",
        )

    def test_mysql_price_alert_runs_after_each_4h_close(self):
        entries = scheduled_entries()
        price_alert_entries = [e for e in entries if "run_price_alert_job.sh" in e]

        self.assertEqual(len(price_alert_entries), 1)
        entry = price_alert_entries[0]
        self.assertTrue(entry.startswith("5 3,7,11,15,19,23 * * 1-5"))
        self.assertIn("/tmp/trade-paxg-price-alert.lock", entry)

    def test_exit_profit_job_is_disabled(self):
        entries = scheduled_entries()

        self.assertFalse(
            any("run_exit_profit_job.sh" in entry for entry in entries),
            "exit-profit monitor must not have an active cron entry",
        )

    def test_liquidity_sweep_job_is_disabled(self):
        entries = scheduled_entries()

        self.assertFalse(
            any("run_liquidity_sweep_job.sh" in entry for entry in entries),
            "liquidity-sweep watch must not have an active cron entry",
        )

    def test_ai_daily_outlook_job_is_scheduled_at_08_00(self):
        entries = scheduled_entries()
        daily_outlook_entries = [e for e in entries if "run_ai_daily_outlook_job.sh" in e]

        self.assertEqual(len(daily_outlook_entries), 1)
        entry = daily_outlook_entries[0]
        self.assertTrue(entry.startswith("0 8 * * 1-5"))
        self.assertIn("/tmp/trade-paxg-ai-daily-outlook.lock", entry)

    def test_ai_session_preparation_job_is_disabled(self):
        entries = scheduled_entries()

        self.assertFalse(
            any("session_preparation" in entry for entry in entries),
            "SESSION_PREPARATION (18:00) is deliberately left disabled",
        )

    def test_ai_session_stage_jobs_are_scheduled(self):
        entries = scheduled_entries()
        expected = {
            "setup_detection": ("0 19 * * 1-5", "/tmp/trade-paxg-ai-setup-detection.lock"),
            "setup_confirmation": ("0 20 * * 1-5", "/tmp/trade-paxg-ai-setup-confirmation.lock"),
            "final_session_decision": (
                "0 21 * * 1-5", "/tmp/trade-paxg-ai-final-session-decision.lock",
            ),
        }
        for stage, (expected_schedule, expected_lock) in expected.items():
            with self.subTest(stage=stage):
                stage_entries = [
                    e for e in entries
                    if f"run_ai_session_stage_job.sh {stage}" in e
                ]
                self.assertEqual(len(stage_entries), 1, f"expected one {stage} cron entry")
                entry = stage_entries[0]
                self.assertTrue(entry.startswith(expected_schedule))
                self.assertIn(expected_lock, entry)

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

    def test_ai_daily_outlook_launcher_runs_the_daily_outlook_stage(self):
        launcher = (ROOT / "run_ai_daily_outlook_job.sh").read_text(encoding="utf-8")

        self.assertIn("ai_analysis.py", launcher)
        self.assertIn('--symbol "${TRADING_SYMBOL:-PAXG/USDT}"', launcher)

    def test_ai_session_stage_launcher_requires_a_stage_argument(self):
        launcher = (ROOT / "run_ai_session_stage_job.sh").read_text(encoding="utf-8")

        self.assertIn("ai_analysis.py", launcher)
        self.assertIn("--stage", launcher)
        # ${1:?...} fails loudly if no stage was passed, rather than
        # silently defaulting to the wrong analysis type.
        self.assertIn('${1:?', launcher)


if __name__ == "__main__":
    unittest.main()
