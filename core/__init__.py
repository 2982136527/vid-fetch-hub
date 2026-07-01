"""Configuration loader for vid-fetch-hub."""

import os
from pathlib import Path
from typing import Any, Dict
import yaml


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
# Docker: also check /config/config.yaml (root-level volume mount)
DOCKER_CONFIG_PATH = Path("/config/config.yaml")


class Config:
    def __init__(self, config_path: str | Path | None = None):
        if config_path:
            path = Path(config_path)
        elif DOCKER_CONFIG_PATH.exists():
            path = DOCKER_CONFIG_PATH
        else:
            path = DEFAULT_CONFIG_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"Config file not found: {path}\n"
                f"Checked: {DEFAULT_CONFIG_PATH} and {DOCKER_CONFIG_PATH}"
            )
        with open(path, "r", encoding="utf-8") as f:
            self._data: Dict[str, Any] = yaml.safe_load(f)

    @property
    def output_dir(self) -> Path:
        return Path(os.path.expanduser(self._data.get("output_dir", "./output")))

    @property
    def organize_by_site(self) -> bool:
        return self._data.get("organize_by_site", True)

    @property
    def check_interval_minutes(self) -> int:
        return self._data.get("check_interval_minutes", 60)

    @property
    def max_pages_per_run(self) -> int:
        return self._data.get("max_pages_per_run", 0)

    @property
    def proxy_host(self) -> str:
        return self._data.get("proxy", {}).get("host", "0.0.0.0")

    @property
    def proxy_port(self) -> int:
        return self._data.get("proxy", {}).get("port", 8383)

    @property
    def proxy_cache_seconds(self) -> int:
        return self._data.get("proxy", {}).get("cache_seconds", 30)

    @property
    def proxy_public_url(self) -> str:
        """Public-facing URL used in STRM files.

        Priority: config value → env var VFH_PUBLIC_URL → auto from host:port
        """
        env_url = os.environ.get("VFH_PUBLIC_URL", "")
        if env_url:
            return env_url.rstrip("/")
        cfg_url = self._data.get("proxy", {}).get("public_url", "")
        if cfg_url:
            return cfg_url.rstrip("/")
        host = self.proxy_host
        port = self.proxy_port
        if host == "0.0.0.0":
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    @property
    def http_proxy(self) -> str:
        """Outgoing HTTP proxy for crawler (e.g. http://proxy:1080).

        Priority: config → env var VFH_HTTP_PROXY
        """
        env = os.environ.get("VFH_HTTP_PROXY", "")
        if env:
            return env
        return self._data.get("http_proxy", "") or ""

    @property
    def https_proxy(self) -> str:
        env = os.environ.get("VFH_HTTPS_PROXY", "")
        if env:
            return env
        return self._data.get("https_proxy", "") or ""

    @property
    def user_agent(self) -> str:
        return self._data.get("request", {}).get(
            "user_agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )

    @property
    def timeout(self) -> int:
        return self._data.get("request", {}).get("timeout", 30)

    @property
    def retry_max(self) -> int:
        return self._data.get("request", {}).get("retry_max", 3)

    @property
    def retry_delay(self) -> int:
        return self._data.get("request", {}).get("retry_delay", 5)

    @property
    def rate_limit(self) -> tuple[float, float]:
        rl = self._data.get("request", {}).get("rate_limit", {})
        return (rl.get("min", 1.0), rl.get("max", 3.0))

    def site_config(self, name: str) -> Dict[str, Any]:
        return self._data.get("sites", {}).get(name, {})

    def is_site_enabled(self, name: str) -> bool:
        return self.site_config(name).get("enabled", False)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)
