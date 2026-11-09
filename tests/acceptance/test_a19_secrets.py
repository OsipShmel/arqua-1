"""А-19 (Б-1): секреты и учётные данные.

Шаги: поиск ключей и учётных данных в исходном коде и репозитории; проверка шаблона
переменных окружения и состава данных; запуск с учётными данными в URL сервиса.
Ожидается: ключей и учётных данных нет; .env в репозитории отсутствует; данные только
открытые и синтетические; запуск с учётными данными в URL отклонён.
"""

import re
import subprocess
from pathlib import Path

import pytest
from conftest import PETSTORE_YAML, ROOT, cli_env, generate, run_cli, scripted_llm

SECRETS = {
    "openai/deepseek key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "aws key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "credential assignment": re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']"
    ),
}


def tracked_files() -> list[Path]:
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout
    return [ROOT / n for n in names.decode().split("\0") if n]


def test_no_keys_in_the_repository() -> None:
    hits = []
    for path in tracked_files():
        if path.suffix in {".lock", ".png", ".jpg", ".pdf"} or not path.is_file():
            continue
        text = path.read_text("utf-8", errors="ignore")
        hits += [
            f"{path.relative_to(ROOT)}: {name}"
            for name, rx in SECRETS.items()
            if rx.search(text)
        ]
    assert hits == []


def test_no_dotenv_is_committed() -> None:
    assert [p for p in tracked_files() if p.name == ".env"] == []
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT)
    assert ignored.returncode == 0, ".env must be git-ignored"


@pytest.mark.parametrize(
    "template", ["backend/agent/.env.example", "backend/testing_tools/.env.example"]
)
def test_env_templates_hold_placeholders_only(template: str) -> None:
    for line in (ROOT / template).read_text("utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(word in key for word in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
            assert value in {"", "your_api_key_here"}, line


@pytest.mark.parametrize(
    "argv",
    [
        ("--base-url", "http://user:pass@127.0.0.1:9"),
        (
            "--base-url",
            "http://127.0.0.1:9",
            "--tools-url",
            "http://user:pass@127.0.0.1:8000/api/v1",
        ),
    ],
)
def test_credentials_in_urls_are_rejected(argv: tuple[str, ...], workdir: Path) -> None:
    run = run_cli("generate", str(PETSTORE_YAML), *argv, env=cli_env(), cwd=workdir)

    assert run.code == 2
    assert "Учётные данные в URL не поддерживаются" in run.stderr
    assert not (workdir / "runs").exists()


def test_credentials_in_contract_url_are_rejected(workdir: Path) -> None:
    with scripted_llm() as llm:
        run = generate(
            "http://user:pass@127.0.0.1:9/openapi.json",
            "http://127.0.0.1:9",
            env=cli_env(llm),
            cwd=workdir,
        )

    assert run.code == 1
    assert "Учётные данные в URL" in run.report["error"]["message"]
    assert (
        "pass@"
        not in (run.run_dir / "report.md").read_text("utf-8").split("## Ошибка")[1]
    )


def test_credentials_in_llm_url_are_rejected(workdir: Path) -> None:
    env = cli_env() | {
        "DEEPCODE_API_KEY": "k",
        "DEEPCODE_MODEL": "m",
        "DEEPCODE_BASE_URL": "https://u:p@llm.example/v1",
    }

    run = run_cli("doctor", "--format", "json", env=env, cwd=workdir)

    assert run.code == 1
    assert "Учётные данные" in run.stdout


@pytest.mark.xfail(
    strict=True, reason="API testing_tools принимает target.base_url с user:password"
)
def test_tools_api_rejects_credentials_in_target(tmp_path: Path) -> None:
    import httpx
    from conftest import inline_contract, tools_service

    with tools_service(tmp_path, tool_mode="stub", tool_delay_s=0) as tools:
        response = httpx.post(
            f"{tools}/runs",
            json={
                "contract": inline_contract(PETSTORE_YAML),
                "target": {"base_url": "http://user:pass@127.0.0.1:9"},
                "tools": [{"tool": "schemathesis"}],
            },
        )

    assert response.status_code == 422
