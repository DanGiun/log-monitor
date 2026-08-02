from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .models import AppConfig


class ConfigStore:
    """Thread-safe, atomically persisted application configuration."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".config" / "log-viewer" / "config.json"
        self._lock = threading.RLock()
        self._config = self._load()

    def _load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            return AppConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
        except Exception:
            backup = self.path.with_suffix(".invalid.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return AppConfig()

    def get(self) -> AppConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def replace(self, config: AppConfig) -> AppConfig:
        with self._lock:
            self._config = config.model_copy(deep=True)
            self._save_locked()
            return self.get()

    def update(self, mutator) -> AppConfig:
        with self._lock:
            candidate = self._config.model_copy(deep=True)
            mutator(candidate)
            self._config = AppConfig.model_validate(candidate.model_dump())
            self._save_locked()
            return self.get()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(self._config.model_dump(mode="json"), indent=2, ensure_ascii=False)
        fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
