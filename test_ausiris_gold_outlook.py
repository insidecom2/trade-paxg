import asyncio
import unittest
from unittest.mock import patch

import requests

from ausiris_gold_outlook import (
    extract_gold_outlook,
    run_ausiris_gold_outlook,
    split_telegram_messages,
)


class FakeNotifier:
    def __init__(self, outcomes=None):
        self.messages = []
        self.outcomes = list(outcomes or [])

    async def send_message(self, text):
        self.messages.append(text)
        return self.outcomes.pop(0) if self.outcomes else True


class ExtractGoldOutlookTests(unittest.TestCase):
    def test_extracts_only_container_with_requested_heading(self):
        html = """
        <div class="parsys-container parsys">
          <h2>ราคาทองและค่าเงิน</h2><p>ไม่ส่งส่วนนี้</p>
        </div>
        <div class="parsys-container parsys">
          <h2><p>มุมมองราคาทอง</p></h2>
          <h3><p>ทองคำเคลื่อนไหว&nbsp;ตามตลาด</p><p>ย่อหน้าสอง</p></h3>
          <script>ไม่ส่งสคริปต์</script>
        </div>
        <div class="parsys-container parsys">
          <h2>มุมมองทิศทางค่าเงินบาท</h2><p>ไม่ส่งค่าเงิน</p>
        </div>
        """
        self.assertEqual(
            extract_gold_outlook(html),
            "มุมมองราคาทอง\n\nทองคำเคลื่อนไหว ตามตลาด\n\nย่อหน้าสอง",
        )

    def test_returns_none_when_target_section_is_missing_or_empty(self):
        self.assertIsNone(
            extract_gold_outlook(
                '<div class="parsys-container parsys"><h2>ข่าวอื่น</h2></div>'
            )
        )
        self.assertIsNone(
            extract_gold_outlook(
                '<div class="parsys-container parsys"><h2>มุมมองราคาทอง</h2></div>'
            )
        )


class SplitTelegramMessagesTests(unittest.TestCase):
    def test_preserves_all_text_and_respects_utf16_size_limit(self):
        source = "ย่อหน้าแรก " + ("💰" * 30) + "\n\n" + ("คำ " * 35)
        chunks = split_telegram_messages(source, max_units=40)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(
            all(len(chunk.encode("utf-16-le")) // 2 <= 40 for chunk in chunks)
        )

    def test_splits_long_unbroken_text_without_loss(self):
        source = "ก" * 35
        chunks = split_telegram_messages(source, max_units=10)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))


class RunAusirisGoldOutlookTests(unittest.TestCase):
    def test_sends_extracted_text(self):
        notifier = FakeNotifier()
        html = (
            '<div class="parsys-container parsys">'
            "<h2>มุมมองราคาทอง</h2><p>บทวิเคราะห์ล่าสุด</p></div>"
        )
        success = asyncio.run(
            run_ausiris_gold_outlook(notifier, fetch_html=lambda: html)
        )
        self.assertTrue(success)
        self.assertEqual(notifier.messages, ["มุมมองราคาทอง\n\nบทวิเคราะห์ล่าสุด"])

    def test_missing_section_sends_nothing(self):
        notifier = FakeNotifier()
        success = asyncio.run(
            run_ausiris_gold_outlook(
                notifier,
                fetch_html=lambda: '<div class="parsys-container parsys">ข่าวอื่น</div>',
            )
        )
        self.assertFalse(success)
        self.assertEqual(notifier.messages, [])

    def test_stops_when_telegram_rejects_a_chunk(self):
        notifier = FakeNotifier(outcomes=[False])
        html = (
            '<div class="parsys-container parsys">'
            "<h2>มุมมองราคาทอง</h2><p>บทวิเคราะห์</p></div>"
        )
        success = asyncio.run(
            run_ausiris_gold_outlook(notifier, fetch_html=lambda: html)
        )
        self.assertFalse(success)
        self.assertEqual(len(notifier.messages), 1)

    def test_http_failure_sends_nothing(self):
        notifier = FakeNotifier()

        def fail_fetch():
            raise requests.Timeout("source timeout")

        success = asyncio.run(
            run_ausiris_gold_outlook(notifier, fetch_html=fail_fetch)
        )
        self.assertFalse(success)
        self.assertEqual(notifier.messages, [])

    def test_missing_telegram_configuration_sends_nothing(self):
        with patch("ausiris_gold_outlook.TelegramNotifier.from_env", return_value=None):
            success = asyncio.run(run_ausiris_gold_outlook())
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
