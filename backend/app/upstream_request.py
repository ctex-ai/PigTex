from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpstreamRequestConfig:
    api_key: str
    base_url: str
    api_provider: str
