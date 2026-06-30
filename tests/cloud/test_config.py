import pytest
from wingman.cloud.config_cloud import CloudConfig, ConfigError

REQUIRED = {
    "DATABASE_URL": "postgresql://u:p@localhost/db",
    "WORKOS_API_KEY": "sk_test",
    "WORKOS_CLIENT_ID": "client_123",
    "WINGMAN_BASE_URL": "https://wingman.example.com",
}

def test_from_env_reads_required(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    cfg = CloudConfig.from_env()
    assert cfg.database_url == REQUIRED["DATABASE_URL"]
    assert cfg.workos_client_id == "client_123"
    assert cfg.sentry_dsn is None

def test_defaults_applied(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    cfg = CloudConfig.from_env()
    assert cfg.max_plans_per_user == 100
    assert cfg.max_tasks_per_plan == 500
    assert cfg.max_batch_size == 50
    assert cfg.max_body_bytes == 256 * 1024

def test_quota_override(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MAX_PLANS_PER_USER", "7")
    assert CloudConfig.from_env().max_plans_per_user == 7

def test_allowed_origins_split(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.com, https://b.com")
    assert CloudConfig.from_env().allowed_origins == ["https://a.com", "https://b.com"]

def test_missing_required_raises(monkeypatch):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ConfigError):
        CloudConfig.from_env()
