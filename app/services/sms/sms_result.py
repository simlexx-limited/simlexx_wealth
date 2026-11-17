from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SmsResult:
    """What a provider tells us about a message it accepted."""
    provider: str
    message_id: Optional[str] = None
    cost: Optional[str] = None
    status: Optional[str] = None
