from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()

def _lock_for(path: str | os.PathLike[str]) -> threading.RLock:
    key = str(Path(path).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())

def load_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    target = Path(path)
    fallback = {} if default is None else default
    if not target.exists():
        return fallback.copy() if isinstance(fallback, (dict, list)) else fallback
    with _lock_for(target):
        try:
            with target.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[STORAGE] Could not read {target}: {exc}")
            return fallback.copy() if isinstance(fallback, (dict, list)) else fallback

def save_json(path: str | os.PathLike[str], data: Any) -> bool:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    with _lock_for(target):
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
            return True
        except OSError as exc:
            print(f"[STORAGE] Could not write {target}: {exc}")
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
