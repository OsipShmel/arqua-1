import json

import pytest

from agent import cli, model


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(model, "_init_env", lambda: None)
    monkeypatch.setenv("DEEPCODE_API_KEY", "never-print-this-secret")
    monkeypatch.setenv("DEEPCODE_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("DEEPCODE_MODEL", "local-test")
    monkeypatch.setenv("DEEPCODE_TEMPERATURE", "0")
    monkeypatch.setenv("DEEPCODE_TIMEOUT", "60")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(model, "ChatOpenAI", lambda **_: pytest.fail("Client created"))


def test_doctor_offline_and_secret_free(settings, capsys):
    assert cli.main(["doctor", "--format", "json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["status"] == "ok" and report["network_checked"] is False
    assert "never-print-this-secret" not in captured.out + captured.err
    assert (
        next(c for c in report["checks"] if c["name"] == "tools")["status"] == "warning"
    )


@pytest.mark.parametrize(
    "variable,value",
    [
        ("DEEPCODE_API_KEY", ""),
        ("DEEPCODE_BASE_URL", "http://user:password@localhost"),
        ("DEEPCODE_TIMEOUT", "nan"),
        ("DEEPCODE_TEMPERATURE", "never-print-this-secret"),
        ("SSL_CERT_FILE", "missing-arqa-certificate.pem"),
    ],
)
def test_doctor_invalid_configuration(settings, monkeypatch, capsys, variable, value):
    monkeypatch.setenv(variable, value)
    assert cli.main(["doctor", "--format", "json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "error"
    assert "never-print-this-secret" not in captured.out + captured.err
    assert "password" not in captured.out + captured.err
