"""Shared loaders for machine-readable prompt packs under repo-level data/."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PromptPackStore:
    """Small TTL cache for prompt-related JSON packs."""

    _data_dir: Optional[Path] = None
    _file_cache: Dict[str, tuple[datetime, Dict[str, Any]]] = {}
    _dir_cache: Dict[str, tuple[datetime, Dict[str, Dict[str, Any]]]] = {}
    DEFAULT_TTL = timedelta(seconds=120)

    @classmethod
    def resolve_data_dir(cls) -> Optional[Path]:
        if cls._data_dir and cls._data_dir.exists():
            return cls._data_dir

        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "data"
            if (candidate / "system_prompts").exists():
                cls._data_dir = candidate
                return candidate
        return None

    @classmethod
    def _is_fresh(cls, loaded_at: datetime, ttl: timedelta) -> bool:
        return (datetime.now() - loaded_at) < ttl

    @staticmethod
    def _read_json_object(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Prompt pack load failed (%s): %s", path, exc)
            return {}
        if isinstance(payload, dict):
            return payload
        logger.warning("Prompt pack file is not a JSON object: %s", path)
        return {}

    @classmethod
    def load_json_file(
        cls,
        *segments: str,
        force_reload: bool = False,
        ttl: Optional[timedelta] = None,
    ) -> Dict[str, Any]:
        data_dir = cls.resolve_data_dir()
        if not data_dir:
            return {}

        path = data_dir.joinpath(*segments)
        cache_key = str(path.resolve())
        cache_ttl = ttl or cls.DEFAULT_TTL
        cached = cls._file_cache.get(cache_key)
        if cached and not force_reload and cls._is_fresh(cached[0], cache_ttl):
            return cached[1]

        payload = cls._read_json_object(path)
        cls._file_cache[cache_key] = (datetime.now(), payload)
        return payload

    @classmethod
    def load_json_dir(
        cls,
        *segments: str,
        force_reload: bool = False,
        ttl: Optional[timedelta] = None,
    ) -> Dict[str, Dict[str, Any]]:
        data_dir = cls.resolve_data_dir()
        if not data_dir:
            return {}

        directory = data_dir.joinpath(*segments)
        cache_key = str(directory.resolve())
        cache_ttl = ttl or cls.DEFAULT_TTL
        cached = cls._dir_cache.get(cache_key)
        if cached and not force_reload and cls._is_fresh(cached[0], cache_ttl):
            return cached[1]

        loaded: Dict[str, Dict[str, Any]] = {}
        if directory.exists():
            for file_path in sorted(directory.glob("*.json")):
                payload = cls._read_json_object(file_path)
                if payload:
                    loaded[file_path.stem] = payload

        cls._dir_cache[cache_key] = (datetime.now(), loaded)
        return loaded
