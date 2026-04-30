"""
fs/object_store.py

File-backed object store for VerdictFS.
Each server persists its fragment + FPCC as a JSON file under DATA_DIR.
Mount DATA_DIR as a Docker volume so data survives container restarts.
"""

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ObjectStore:
    def __init__(self, data_dir: str = "/app/data") -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        logger.info("ObjectStore ready at %s", self._dir)

    def _path(self, key: str) -> Path:
        safe = hashlib.sha256(key.encode()).hexdigest()
        return self._dir / f"{safe}.json"

    def put(self, key: str, fragment: dict, fpcc: dict) -> None:
        payload = {"fragment": fragment, "fpcc": fpcc}
        self._path(key).write_text(json.dumps(payload), encoding="utf-8")
        logger.debug("persisted key=%s", key)

    def get(self, key: str) -> tuple[dict, dict] | None:
        path = self._path(key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload["fragment"], payload["fpcc"]

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
