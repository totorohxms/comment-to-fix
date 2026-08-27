"""Central configuration — the only place environment variables are read.

Precedence: real environment variables > .env file at the repo root > the
defaults here. Copy .env.example to .env to customize a local setup; deploys
set real env vars and need no file. Modules import `settings`, never os.environ.
"""

import os
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

def _load_env_file(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE lines, # comments). Never overrides real
    environment variables."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

_load_env_file(_ROOT / ".env")

@dataclass(frozen=True)
class Settings:
    # database
    db_path: str
    # agent queue
    agent_max_inflight: int
    task_max_attempts: int
    task_lease_s: float
    reaper_interval_s: float
    task_timeout_s: float
    agent_launcher: str
    # comment rate limit (per user)
    rate_limit_max: int
    rate_limit_window_s: float
    # api
    cors_origins: list[str]
    # logging
    log_level: str
    log_format: str
    # real PR integration (optional)
    pr_service: str          # auto | fake | github
    github_repo: str
    github_token: str
    github_auto_merge: bool
    app_base_url: str
    # metrics
    statsd_host: str
    statsd_port: int
    statsd_prefix: str
    statsd_enabled: bool

def _build() -> Settings:
    env = os.environ.get
    return Settings(
        db_path=env("CTF_DB", str(_ROOT / "data" / "ctf.db")),
        agent_max_inflight=int(env("AGENT_MAX_INFLIGHT", "2")),
        task_max_attempts=int(env("TASK_MAX_ATTEMPTS", "3")),
        task_lease_s=float(env("TASK_LEASE_S", "15")),
        reaper_interval_s=float(env("REAPER_INTERVAL_S", "30")),
        task_timeout_s=float(env("TASK_TIMEOUT_S", "300")),
        agent_launcher=env("AGENT_LAUNCHER", "fake-claude"),
        rate_limit_max=int(env("RATE_LIMIT_MAX", "8")),
        rate_limit_window_s=float(env("RATE_LIMIT_WINDOW_S", "30")),
        cors_origins=[o.strip() for o in env("CORS_ORIGINS", "http://localhost:3000").split(",")],
        log_level=env("LOG_LEVEL", "INFO"),
        log_format=env("LOG_FORMAT", "text"),
        pr_service=env("PR_SERVICE", "auto"),
        github_repo=env("GITHUB_REPO", ""),
        github_token=env("GITHUB_TOKEN", ""),
        github_auto_merge=env("GITHUB_AUTO_MERGE", "1") == "1",
        app_base_url=env("APP_BASE_URL", ""),
        statsd_host=env("STATSD_HOST", "127.0.0.1"),
        statsd_port=int(env("STATSD_PORT", "8125")),
        statsd_prefix=env("STATSD_PREFIX", "ctf"),
        statsd_enabled=env("STATSD_ENABLED", "1") == "1",
    )

settings = _build()
