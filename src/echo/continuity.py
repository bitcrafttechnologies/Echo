"""Bounded conversational continuity and lightweight pending intentions."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class ConversationTurn:
    user: str
    assistant: str
    created_at: str = field(default_factory=_now_iso)

    def messages(self) -> tuple[dict[str, str], ...]:
        return (
            {"role": "user", "content": self.user},
            {"role": "assistant", "content": self.assistant},
        )


@dataclass(slots=True)
class PendingIntent:
    capability: str
    reason: str
    parameters: dict[str, Any] = field(default_factory=dict)
    status: str = "awaiting_confirmation"
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "capability_action",
            "capability": self.capability,
            "reason": self.reason,
            "parameters": dict(self.parameters),
            "status": self.status,
            "created_at": self.created_at,
        }


class ConversationState:
    """Session-scoped dialogue state with a deterministic token budget."""

    _CONFIRMATION = re.compile(
        r"^\s*(?:yes|yeah|yep|sure|okay|ok|go ahead|go for it|do it|please do)"
        r"[.!\s]*$",
        re.I,
    )
    _REFERENCE = re.compile(
        r"\b(?:it|that|this|them|the other one|what about that|tell me more|check it)\b",
        re.I,
    )

    def __init__(self, *, max_tokens: int = 1500, max_turns: int = 12) -> None:
        if max_tokens < 64 or max_turns < 1:
            raise ValueError("conversation bounds are too small")
        self.max_tokens = max_tokens
        self._turns: deque[ConversationTurn] = deque(maxlen=max_turns)
        self.pending_intent: PendingIntent | None = None

    def add_turn(self, user: str, assistant: str) -> None:
        self._turns.append(ConversationTurn(user=user.strip(), assistant=assistant.strip()))

    def immediate_messages(self, current_user: str) -> tuple[dict[str, str], ...]:
        messages: list[dict[str, str]] = []
        used = self._estimated_tokens(current_user)
        for turn in reversed(self._turns):
            pair = turn.messages()
            cost = sum(self._estimated_tokens(item["content"]) for item in pair)
            if used + cost > self.max_tokens:
                break
            messages[0:0] = pair
            used += cost
        return tuple(messages)

    @staticmethod
    def _estimated_tokens(text: str) -> int:
        """Conservative provider-neutral token estimate for hard prompt budgeting."""

        return max(1, (len(text.encode("utf-8")) + 3) // 4)

    def interpretation(self, text: str) -> dict[str, Any]:
        pending = self.pending_intent
        if pending is not None and self._CONFIRMATION.fullmatch(text):
            return {
                "kind": "pending_intent_confirmation",
                "resolved": True,
                "referent": pending.to_dict(),
            }
        if self._REFERENCE.search(text) and self._turns:
            last = self._turns[-1]
            return {
                "kind": "immediate_dialogue_reference",
                "resolved": True,
                "referent": {
                    "user": last.user,
                    "assistant": last.assistant,
                },
            }
        return {"kind": "literal", "resolved": True, "referent": None}

    def confirmed_action(self, text: str) -> tuple[str, dict[str, Any]] | None:
        if self.pending_intent is None or not self._CONFIRMATION.fullmatch(text):
            return None
        return self.pending_intent.capability, dict(self.pending_intent.parameters)

    def complete_pending(self) -> None:
        if self.pending_intent is not None:
            self.pending_intent.status = "completed"
            self.pending_intent = None

    def detect_pending_offer(
        self,
        assistant_text: str,
        inventory: Mapping[str, Any] | None,
    ) -> PendingIntent | None:
        if "?" not in assistant_text or not re.search(
            r"\b(?:want me to|shall i|should i|would you like me to|can i)\b",
            assistant_text,
            re.I,
        ):
            return None
        lowered = assistant_text.casefold()
        for item in (inventory or {}).get("capabilities", ()):
            name = str(item.get("name", ""))
            if not name or item.get("available") is not True:
                continue
            terms = {part for part in re.findall(r"[a-z0-9]+", name.casefold()) if len(part) > 2}
            description = str(item.get("description", "")).casefold()
            if any(term in lowered for term in terms) or (
                description and any(term in lowered for term in re.findall(r"[a-z0-9]+", description) if len(term) > 4)
            ):
                intent = PendingIntent(
                    capability=name,
                    reason="assistant_capability_offer",
                )
                self.pending_intent = intent
                return intent
        return None
