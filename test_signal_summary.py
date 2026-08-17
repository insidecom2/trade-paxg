import unittest

from main import build_signal_summary
from models import Signal


class SignalSummaryTests(unittest.TestCase):
    def test_formats_breakout_watch_message(self):
        signal = Signal(
            action="HOLD",
            position="RESISTANCE",
            price=4369.61,
            reason="Breakout watch active",
            status="BREAKOUT_WATCH",
            volume_ratio=0.98,
            volume_status="THIN",
        )

        message = build_signal_summary(
            "PAXG/USDT",
            "4h",
            signal,
            support=4313.81,
            resistance=4358.91,
            current_price=4372.87,
            next_resistance=4410.00,
            strategy_state={"uptrend": True},
        )

        self.assertEqual(
            message,
            "\n".join(
                [
                    "PAXG 4H | HOLD | BREAKOUT_WATCH",
                    "",
                    "Price: $4372.87",
                    "Close: $4369.61",
                    "",
                    "Resistance: $4358.91 ✅",
                    "Next Resistance: $4410.00",
                    "Support: $4313.81",
                    "Next Support: N/A",
                    "Headroom: 0.85% ✅",
                    "",
                    "Trend: UP ✅",
                    "Volume: 0.98x ❌",
                    "Pattern: NONE ❌",
                    "Retest: WAIT ⏳",
                    "",
                    "Score: 3/6",
                    "",
                    "Reason:",
                    "Breakout occurred, but volume and candle confirmation",
                    "are still insufficient. Waiting for confirmation/retest.",
                ]
            ),
        )

    def test_formats_bollinger_status_for_enabled_strategy(self):
        signal = Signal(
            action="HOLD",
            position="RESISTANCE",
            price=110.0,
            reason="waiting",
            status="BREAKOUT_WATCH",
            bband_le=False,
            bband_se=False,
            bband_upper=108.0,
            bband_middle=100.0,
            bband_lower=92.0,
        )

        message = build_signal_summary(
            "PAXG/USDT", "4h", signal, 90.0, 100.0, 110.0, next_resistance=130.0
        )

        self.assertIn("BBandLE: ❌", message)
        self.assertIn("BBandSE: ❌", message)
        self.assertIn("BBands: Upper $108.00 | Middle $100.00 | Lower $92.00", message)
        self.assertIn("Score: 2/7", message)

    def test_missing_next_resistance_and_volume_are_safe(self):
        signal = Signal(
            action="HOLD",
            position="NEUTRAL",
            price=100.0,
            reason="No signal",
            status="NEUTRAL",
        )

        message = build_signal_summary(
            "PAXG/USDT",
            "4h",
            signal,
            support=99.0,
            resistance=101.0,
            current_price=100.0,
        )

        self.assertIn("Next Support: N/A", message)
        self.assertIn("Next Resistance: N/A", message)
        self.assertIn("Headroom: N/A ❌", message)
        self.assertIn("Volume: N/A ❌", message)


if __name__ == "__main__":
    unittest.main()
