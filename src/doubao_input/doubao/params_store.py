"""Persist ASR params (cookies, device_id, web_id) to a JSON file.

Mirrors ASRParamsStore.swift.
Location: $XDG_CONFIG_HOME/doubao-say/asr_params.json
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass

from doubao_input.doubao.config import get_params_path

logger = logging.getLogger(__name__)


@dataclass
class ASRParams:
    """Parameters needed to establish a WSS ASR connection."""

    cookies: dict[str, str]
    device_id: str
    web_id: str

    def validate(self) -> None:
        if not isinstance(self.cookies, dict) or not self.cookies:
            raise ValueError("Missing sign-in cookies")
        if any(not isinstance(k, str) or not k or not isinstance(v, str)
               for k, v in self.cookies.items()):
            raise ValueError("Invalid sign-in cookies")
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.device_id, self.web_id)):
            raise ValueError("Missing sign-in identifiers")

    @property
    def cookie_header(self) -> str:
        """Build the Cookie header string for HTTP/WSS requests."""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())


class ParamsStore:
    """Persist ASR params to JSON file."""

    @staticmethod
    def save(params: ASRParams) -> None:
        """Atomically save owner-only credentials; propagate failures to callers."""
        params.validate()
        path = get_params_path()
        data = json.dumps(asdict(params), ensure_ascii=False, indent=2)
        fd, temporary = tempfile.mkstemp(prefix=".asr-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def load() -> ASRParams | None:
        path = get_params_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            params = ASRParams(**data)
            params.validate()
            path.chmod(0o600)
            return params
        except (OSError, TypeError, ValueError):
            logger.warning("Saved sign-in could not be read; please sign in again")
            return None

    @staticmethod
    def clear() -> None:
        get_params_path().unlink(missing_ok=True)
        logger.info("Cleared saved params")

    @staticmethod
    def has_saved() -> bool:
        return ParamsStore.load() is not None
