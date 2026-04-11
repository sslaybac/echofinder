import json
from pathlib import Path

import platformdirs


class SessionState:
    _APP_NAME = "echofinder"
    _CONFIG_FILE = "session.json"

    def __init__(self) -> None:
        config_dir = Path(platformdirs.user_config_dir(self._APP_NAME))
        config_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = config_dir / self._CONFIG_FILE
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._config_path.exists():
            try:
                with open(self._config_path) as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        try:
            with open(self._config_path, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    def get_root(self) -> str | None:
        return self._data.get("root")

    def set_root(self, path: str) -> None:
        self._data["root"] = path
        self._save()

    def get_expansion_state(self) -> list[str]:
        value = self._data.get("expanded_paths", [])
        return value if isinstance(value, list) else []

    def set_expansion_state(self, paths: list[str]) -> None:
        self._data["expanded_paths"] = paths
        self._save()

    def clear_expansion_state(self) -> None:
        self._data.pop("expanded_paths", None)
        self._save()
