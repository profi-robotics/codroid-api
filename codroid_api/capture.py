import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class CaptureMessage:
    direction: str
    data: Dict[str, Any]
    timestamp: float
    opcode: int

    @property
    def action(self) -> Optional[str]:
        value = self.data.get("action")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class CodroidCapture:
    messages: List[CaptureMessage]

    @property
    def sends(self) -> List[CaptureMessage]:
        return [message for message in self.messages if message.direction == "send"]

    @property
    def receives(self) -> List[CaptureMessage]:
        return [message for message in self.messages if message.direction == "receive"]

    def send_payloads(self) -> List[Dict[str, Any]]:
        return [message.data for message in self.sends]

    def actions(self, direction: Optional[str] = "send") -> List[str]:
        messages = self.messages if direction is None else [
            message for message in self.messages if message.direction == direction
        ]
        actions = [message.action for message in messages if message.action]
        return sorted(set(actions))

    def action_counts(self, direction: Optional[str] = "send") -> Counter[str]:
        messages = self.messages if direction is None else [
            message for message in self.messages if message.direction == direction
        ]
        return Counter(message.action for message in messages if message.action)


def load_capture(path: str | Path) -> CodroidCapture:
    payload = json.loads(Path(path).read_text())
    entries = payload.get("log", {}).get("entries", [])
    messages: List[CaptureMessage] = []

    for entry in entries:
        ws_messages = entry.get("_webSocketMessages")
        if not ws_messages:
            continue
        for message in ws_messages:
            try:
                data = json.loads(message.get("data", ""))
            except json.JSONDecodeError:
                continue
            messages.append(
                CaptureMessage(
                    direction=message.get("type", ""),
                    data=data,
                    timestamp=float(message.get("time", 0.0)),
                    opcode=int(message.get("opcode", 0)),
                )
            )

    return CodroidCapture(messages=messages)


def extract_send_messages(messages: Iterable[CaptureMessage]) -> List[Dict[str, Any]]:
    return [message.data for message in messages if message.direction == "send"]
