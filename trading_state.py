import json
import logging
import fcntl
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class TradingStateStore:
    """Persists signal state per symbol and timeframe without storing secrets."""

    def __init__(self, path: Path = Path("trading_state.json")):
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")

    def _read_unlocked(self) -> Dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as state_file:
                data = json.load(state_file)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Unable to read trading state %s: %s", self.path, exc)
            return {}

    def _read(self) -> Dict[str, Any]:
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
            return self._read_unlocked()

    def get(self, key: str) -> Dict[str, Any]:
        value = self._read().get(key, {})
        return value if isinstance(value, dict) else {}

    def save(self, key: str, state: Dict[str, Any]) -> None:
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            data = self._read_unlocked()
            data[key] = state
            temporary_path = self.path.with_name(self.path.name + ".tmp")
            try:
                with temporary_path.open("w", encoding="utf-8") as state_file:
                    json.dump(data, state_file, indent=2, sort_keys=True)
                    state_file.write("\n")
                temporary_path.replace(self.path)
            except OSError as exc:
                logger.error("Unable to save trading state %s: %s", self.path, exc)
