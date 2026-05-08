"""
IB adapter configuration.

Reads from environment variables and .env files.
All values have sensible defaults for paper trading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """Load .env file from CWD or project root if it exists. No external dependency."""
    for candidate in [Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env"]:
        if candidate.is_file():
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("\"'")
                    # Only set if not already in env (real env takes precedence)
                    if key and key not in os.environ:
                        os.environ[key] = value
            break


# Load on import so config picks up .env values
_load_dotenv()


# Port suggestions by mode
PORT_MAP = {
    "paper": 7497,
    "live": 7496,
    "gateway_paper": 4002,
    "gateway_live": 4001,
}


@dataclass
class IBConfig:
    """
    Interactive Brokers connection configuration.

    Reads from environment variables:
        IB_HOST       — TWS/Gateway host        (DEFAULT: 127.0.0.1)
        IB_PORT       — API socket port          (DEFAULT: 7497 for paper)
        IB_CLIENT_ID  — API client identifier    (DEFAULT: 1)
        IB_ACCOUNT    — IB account ID            (DEFAULT: empty, uses first)
        IB_MODE       — paper or live            (DEFAULT: paper)
        IB_TIMEOUT    — connection timeout secs  (DEFAULT: 10)
        IB_READONLY   — read-only mode           (DEFAULT: false)
    """

    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    account: str = ""
    mode: str = "paper"
    timeout: int = 10
    readonly: bool = False

    @classmethod
    def from_env(cls) -> IBConfig:
        """Build config from environment variables."""
        mode = os.getenv("IB_MODE", "paper").lower()

        # Determine port: explicit IB_PORT wins, otherwise derive from mode
        port_str = os.getenv("IB_PORT", "")
        if port_str:
            port = int(port_str)
        else:
            port = PORT_MAP.get(mode, 7497)

        return cls(
            host=os.getenv("IB_HOST", "127.0.0.1"),
            port=port,
            client_id=int(os.getenv("IB_CLIENT_ID", "1")),
            account=os.getenv("IB_ACCOUNT", ""),
            mode=mode,
            timeout=int(os.getenv("IB_TIMEOUT", "10")),
            readonly=os.getenv("IB_READONLY", "false").lower() in ("true", "1", "yes"),
        )

    @property
    def is_paper(self) -> bool:
        return self.mode in ("paper", "gateway_paper")

    @property
    def is_live(self) -> bool:
        return self.mode in ("live", "gateway_live")

    def describe(self) -> str:
        """Human-readable config summary."""
        return (
            f"IB Config: host={self.host}, port={self.port}, "
            f"client_id={self.client_id}, mode={self.mode}, "
            f"account={self.account or '(auto)'}, "
            f"readonly={self.readonly}"
        )
