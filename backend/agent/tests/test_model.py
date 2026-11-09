import pytest

from agent import model


def test_model_settings_and_certificates(monkeypatch):
    monkeypatch.setattr(model, "_init_env", lambda: None)
    monkeypatch.setenv("DEEPCODE_API_KEY", "test-key")
    monkeypatch.setenv("DEEPCODE_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("DEEPCODE_MODEL", "qa-test")
    monkeypatch.setenv("DEEPCODE_TIMEOUT", "12")
    monkeypatch.setenv("DEEPCODE_TEMPERATURE", "0.3")
    monkeypatch.setenv("SSL_CERT_FILE", "custom.pem")
    monkeypatch.setattr(model, "ChatOpenAI", lambda **kwargs: kwargs)
    values = model.build_model()
    assert values["timeout"] == 12 and values["temperature"] == 0.3
    import os

    assert os.environ["SSL_CERT_FILE"] == "custom.pem"


def test_dotenv_current_directory_preserves_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DEEPCODE_MODEL=file-model\nDEEPCODE_TIMEOUT=21\n")
    monkeypatch.setenv("DEEPCODE_MODEL", "process-model")
    monkeypatch.delenv("DEEPCODE_TIMEOUT", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", "custom.pem")
    # Prevent another .env above the installed source tree from affecting this test.
    monkeypatch.setattr(model, "__file__", str(tmp_path / "src/model.py"))
    model._init_env()
    import os

    assert os.environ["DEEPCODE_MODEL"] == "process-model"
    assert os.environ["DEEPCODE_TIMEOUT"] == "21"
    assert os.environ["SSL_CERT_FILE"] == "custom.pem"


def test_missing_configuration_fails_before_model_creation(monkeypatch):
    monkeypatch.setattr(model, "_init_env", lambda: None)
    for key in ("DEEPCODE_API_KEY", "DEEPCODE_BASE_URL", "DEEPCODE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        model, "ChatOpenAI", lambda **kwargs: pytest.fail("Network model created")
    )
    with pytest.raises(RuntimeError, match="DEEPCODE_API_KEY"):
        model.build_model()
