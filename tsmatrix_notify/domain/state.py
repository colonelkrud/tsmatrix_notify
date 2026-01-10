from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AppState:
    join_times: dict[str, float] = field(default_factory=dict)
    client_names: dict[str, str] = field(default_factory=dict)
    good_count: int = 0
    bad_count: int = 0
