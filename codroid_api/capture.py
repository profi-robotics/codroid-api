import json
from collections import Counter
from dataclasses import dataclass, field
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
class CaptureHttpEntry:
    method: str
    url: str
    request_body: Optional[str]
    response_status: int
    response_body: Optional[str]
    timestamp: float

    def request_json(self) -> Optional[Dict[str, Any]]:
        if not self.request_body:
            return None
        try:
            data = json.loads(self.request_body)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class CodroidCapture:
    messages: List[CaptureMessage]
    http_entries: List[CaptureHttpEntry] = field(default_factory=list)

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

    def http_requests(
        self,
        *,
        method: Optional[str] = None,
        url_contains: Optional[str] = None,
    ) -> List[CaptureHttpEntry]:
        entries = self.http_entries
        if method is not None:
            expected_method = method.upper()
            entries = [
                entry for entry in entries
                if entry.method.upper() == expected_method
            ]
        if url_contains is not None:
            entries = [
                entry for entry in entries
                if url_contains in entry.url
            ]
        return entries

    def project_edit_payloads(self) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        for url_part in ("/robot/project/edit", "/robot/project/bak"):
            for entry in self.http_requests(
                method="POST",
                url_contains=url_part,
            ):
                payload = entry.request_json()
                if payload is not None:
                    payloads.append(payload)
        return payloads


def load_capture(path: str | Path) -> CodroidCapture:
    payload = json.loads(Path(path).read_text())
    entries = payload.get("log", {}).get("entries", [])
    messages: List[CaptureMessage] = []
    http_entries: List[CaptureHttpEntry] = []

    for entry in entries:
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        request_body = (request.get("postData") or {}).get("text")
        response_body = (response.get("content") or {}).get("text")
        http_entries.append(
            CaptureHttpEntry(
                method=request.get("method", ""),
                url=request.get("url", ""),
                request_body=request_body,
                response_status=int(response.get("status", 0) or 0),
                response_body=response_body,
                timestamp=float(entry.get("startedDateTimeUnix", 0.0) or 0.0),
            )
        )
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

    return CodroidCapture(messages=messages, http_entries=http_entries)


def extract_send_messages(messages: Iterable[CaptureMessage]) -> List[Dict[str, Any]]:
    return [message.data for message in messages if message.direction == "send"]
