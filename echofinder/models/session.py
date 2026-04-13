import json
from pathlib import Path

import platformdirs


class SessionState:
    """Persists user session data (root path, expansion state) between launches.

    Data is stored as JSON in the platform-appropriate user config directory.
    Corrupt or missing files are silently treated as an empty session.

    Attributes:
        _config_path: Absolute path to the ``session.json`` file.
        _data: In-memory dict mirroring the JSON file contents.
    """

    _APP_NAME = "echofinder"
    _CONFIG_FILE = "session.json"

    def __init__(self) -> None:
        """Initialise session state, creating the config directory if needed."""
        config_dir = Path(platformdirs.user_config_dir(self._APP_NAME))
        config_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = config_dir / self._CONFIG_FILE
        self._data: dict = self._load()

    def _load(self) -> dict:
        """Read and return the JSON session file, or ``{}`` on any error."""
        if self._config_path.exists():
            try:
                with open(self._config_path) as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        """Persist ``_data`` to disk; silently ignores write errors."""
        try:
            with open(self._config_path, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    def get_root(self) -> str | None:
        """Return the last saved root directory path, or ``None`` if unset.

        Returns:
            The root path string, or ``None``.
        """
        return self._data.get("root")

    def set_root(self, path: str) -> None:
        """Persist *path* as the current root directory.

        Args:
            path: Absolute path string to store as the root.
        """
        self._data["root"] = path
        self._save()

    def get_expansion_state(self) -> list[str]:
        """Return the list of expanded folder path strings, or ``[]`` if unset.

        Returns:
            A list of absolute path strings for expanded directories.
        """
        value = self._data.get("expanded_paths", [])
        return value if isinstance(value, list) else []

    def set_expansion_state(self, paths: list[str]) -> None:
        """Persist the list of expanded folder paths.

        Args:
            paths: Absolute path strings of currently expanded directories.
        """
        self._data["expanded_paths"] = paths
        self._save()

    def clear_expansion_state(self) -> None:
        """Remove the stored expansion state (called when the root changes)."""
        self._data.pop("expanded_paths", None)
        self._save()
