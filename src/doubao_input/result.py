"""One in-memory recovery slot. Never persists speech or credentials."""
from dataclasses import dataclass


@dataclass
class RecentResult:
    text: str = ""
    status: str = "empty"

    def keep(self, text, status="ready"):
        self.text, self.status = text.strip(), status if text.strip() else "empty"

    def clear(self):
        self.text, self.status = "", "empty"
