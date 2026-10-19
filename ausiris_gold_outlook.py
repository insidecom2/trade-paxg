"""Send Ausiris's gold outlook text to Telegram on its weekday schedule."""

import argparse
import asyncio
import logging
import re
from html.parser import HTMLParser
from typing import Callable, Optional

import requests
from dotenv import load_dotenv

from telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)

PAGE_URL = (
    "http://www.ausiris.co.th/content/index/research/"
    "research-gold/gold-main.html"
)
TARGET_HEADING = "มุมมองราคาทอง"
TELEGRAM_MAX_TEXT_UNITS = 4000
HTTP_TIMEOUT = (5, 20)
MAX_HTML_BYTES = 2 * 1024 * 1024

load_dotenv()


class _AusirisSectionParser(HTMLParser):
    """Collect visible text from matching divs that contain the target heading."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_stack: list[Optional[dict]] = []
        self.active_sections: list[dict] = []
        self.heading_stack: list[dict] = []
        self.hidden_depth = 0
        self.sections: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden_depth += 1
        if tag == "div":
            attributes = dict(attrs)
            classes = set((attributes.get("class") or "").split())
            section = None
            if {"parsys-container", "parsys"}.issubset(classes):
                section = {"parts": [], "body_parts": [], "has_heading": False}
                self.active_sections.append(section)
            self.div_stack.append(section)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_stack.append({"tag": tag, "parts": []})
        if tag in {"p", "li", "br"}:
            self._append_break()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            for index in range(len(self.heading_stack) - 1, -1, -1):
                heading = self.heading_stack.pop(index)
                if heading["tag"] == tag:
                    heading_text = self._clean_text("".join(heading["parts"]))
                    if TARGET_HEADING in heading_text:
                        for section in self.active_sections:
                            section["has_heading"] = True
                    else:
                        for section in self.active_sections:
                            section["body_parts"].append(heading_text)
                    break
        if tag == "div" and self.div_stack:
            section = self.div_stack.pop()
            if section is not None:
                self.active_sections.remove(section)
                if section["has_heading"]:
                    value = self._clean_text("".join(section["parts"]))
                    body = self._clean_text("".join(section["body_parts"]))
                    if value and body:
                        self.sections.append(value)
        if tag in {"script", "style", "noscript"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.hidden_depth:
            return
        if not data.strip():
            return
        for section in self.active_sections:
            section["parts"].append(data.replace("\xa0", " "))
            if not self.heading_stack:
                section["body_parts"].append(data.replace("\xa0", " "))
        for heading in self.heading_stack:
            heading["parts"].append(data.replace("\xa0", " "))

    def _append_break(self) -> None:
        if self.hidden_depth:
            return
        for section in self.active_sections:
            section["parts"].append("\n\n")
            section["body_parts"].append("\n\n")

    @staticmethod
    def _clean_text(value: str) -> str:
        paragraphs = [
            re.sub(r"\s+", " ", paragraph).strip()
            for paragraph in value.split("\n\n")
        ]
        paragraphs = [paragraph for paragraph in paragraphs if paragraph]
        return "\n\n".join(paragraphs)


def extract_gold_outlook(html: str) -> Optional[str]:
    """Return text from the parsys container headed “มุมมองราคาทอง”."""
    parser = _AusirisSectionParser()
    parser.feed(html)
    parser.close()
    return parser.sections[0] if parser.sections else None


def _text_units(value: str) -> int:
    """Count UTF-16 code units conservatively for Telegram's text limit."""
    return len(value.encode("utf-16-le")) // 2


def split_telegram_messages(
    text: str, max_units: int = TELEGRAM_MAX_TEXT_UNITS
) -> list[str]:
    """Split near paragraphs or spaces while preserving every source character."""
    if max_units < 1:
        raise ValueError("max_units must be positive")
    messages: list[str] = []
    offset = 0
    while offset < len(text):
        end = offset
        units = 0
        while end < len(text):
            char_units = _text_units(text[end])
            if units + char_units > max_units:
                break
            units += char_units
            end += 1
        if end == len(text):
            messages.append(text[offset:end])
            break
        if end == offset:
            raise ValueError("max_units is too small for a Unicode character")

        candidate = text[offset:end]
        paragraph_break = candidate.rfind("\n\n")
        space_break = candidate.rfind(" ")
        if paragraph_break >= len(candidate) // 2:
            end = offset + paragraph_break + 2
        elif space_break >= len(candidate) // 2:
            end = offset + space_break + 1
        messages.append(text[offset:end])
        offset = end
    return messages


def fetch_gold_outlook_html() -> str:
    with requests.get(PAGE_URL, timeout=HTTP_TIMEOUT, stream=True) as response:
        response.raise_for_status()
        chunks = []
        total_bytes = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            total_bytes += len(chunk)
            if total_bytes > MAX_HTML_BYTES:
                raise ValueError("Ausiris page exceeded the 2 MiB response limit")
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")


async def run_ausiris_gold_outlook(
    notifier: Optional[TelegramNotifier] = None,
    fetch_html: Optional[Callable[[], str]] = None,
) -> bool:
    """Fetch, extract, and deliver the current Ausiris outlook."""
    try:
        notifier = notifier if notifier is not None else TelegramNotifier.from_env()
        if notifier is None:
            logger.error("Telegram configuration is missing; Ausiris outlook was not sent")
            return False

        html = await asyncio.to_thread(fetch_html or fetch_gold_outlook_html)
        outlook = extract_gold_outlook(html)
        if not outlook:
            logger.error("Ausiris page has no non-empty %s section", TARGET_HEADING)
            return False

        for message in split_telegram_messages(outlook):
            if not await notifier.send_message(message):
                logger.error("Telegram rejected an Ausiris gold outlook message")
                return False
        logger.info("Ausiris gold outlook sent in %d Telegram message(s)", len(split_telegram_messages(outlook)))
        return True
    except requests.RequestException:
        logger.exception("Could not fetch the Ausiris gold outlook page")
        return False
    except Exception:
        logger.exception("Ausiris gold outlook job failed")
        return False


def parse_args() -> argparse.Namespace:
    return argparse.ArgumentParser(
        description="Send Ausiris's gold outlook to Telegram"
    ).parse_args()


if __name__ == "__main__":
    parse_args()
    raise SystemExit(0 if asyncio.run(run_ausiris_gold_outlook()) else 1)
