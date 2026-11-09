"""Finished runs on disk, one directory per run, named by start time.

    <results_dir>/<YYYYmmddTHHMMSSZ>_<run_id>/
        result.json      — RunResult, as served by GET /runs/{id}/result
        report.md        — the Markdown report
        request.json     — the request, secret header values masked
        artifacts/       — raw tool reports
        artifacts.json   — {name: media_type}

Runs evicted from memory or left from a previous process are served from here.
"""

import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from testing_tools.api.schemas import RunCreateRequest, RunLinks, RunResult
from testing_tools.app.contract import LoadedContract
from testing_tools.app.store import REPORT_ARTIFACT, Artifact, RunRecord, ToolRun

MASK = "***"

log = logging.getLogger(__name__)


class RunArchive:
    def __init__(self, root: Path, api_prefix: str) -> None:
        self.root = root
        self._api_prefix = api_prefix

    def save(self, record: RunRecord) -> Path:
        stamp = (record.started_at or record.created_at).strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.root / f"{stamp}_{record.run_id}"
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        (run_dir / "result.json").write_text(record.result_view().model_dump_json(indent=2))
        (run_dir / "request.json").write_text(
            json.dumps(masked_request(record.request), ensure_ascii=False, indent=2)
        )
        media_types: dict[str, str] = {}
        for name, artifact in record.artifacts.items():
            if name == REPORT_ARTIFACT:
                (run_dir / "report.md").write_bytes(artifact.content)
                continue
            (artifacts_dir / name).write_bytes(artifact.content)
            media_types[name] = artifact.media_type
        (run_dir / "artifacts.json").write_text(json.dumps(media_types, indent=2))
        log.info("Run %s saved to %s", record.run_id, run_dir)
        return run_dir

    def load(self, run_id: UUID) -> RunRecord | None:
        run_dir = next(iter(sorted(self.root.glob(f"*_{run_id}"))), None)
        if run_dir is None:
            return None
        try:
            return self._load(run_id, run_dir)
        except (OSError, ValueError) as exc:
            log.warning("Run %s in %s is unreadable: %s", run_id, run_dir, exc)
            return None

    def _load(self, run_id: UUID, run_dir: Path) -> RunRecord:
        result = RunResult.model_validate_json((run_dir / "result.json").read_text())
        request = RunCreateRequest.model_validate_json((run_dir / "request.json").read_text())
        media_types: dict[str, str] = json.loads((run_dir / "artifacts.json").read_text())
        artifacts = {
            name: Artifact(media_type, (run_dir / "artifacts" / name).read_bytes())
            for name, media_type in media_types.items()
        }
        report = run_dir / "report.md"
        if report.exists():
            artifacts[REPORT_ARTIFACT] = Artifact("text/markdown", report.read_bytes())
        assert result.contract is not None  # runs are only created after the contract loaded
        base = f"{self._api_prefix}/runs/{run_id}"
        return RunRecord(
            request=request,
            contract=LoadedContract(info=result.contract, operations=()),
            links=RunLinks(self=base, result=f"{base}/result", cancel=f"{base}/cancel"),
            run_id=run_id,
            created_at=result.created_at,
            state=result.state,
            started_at=result.started_at,
            finished_at=result.finished_at,
            error=result.error,
            tools=[
                ToolRun(
                    config=config,
                    state=tool.state,
                    started_at=tool.started_at,
                    finished_at=tool.finished_at,
                    progress=1.0,
                    error=tool.error,
                    result=tool,
                )
                for config, tool in zip(request.tools, result.tools, strict=True)
            ],
            artifacts=artifacts,
            archived_result=result,
        )


def masked_request(request: RunCreateRequest) -> dict[str, Any]:
    """The request as JSON with every caller-supplied header value replaced by ***."""
    data = request.model_dump(mode="json")

    def mask(headers: dict[str, Any]) -> dict[str, Any]:
        return {name: MASK for name in headers}

    data["target"]["headers"] = mask(data["target"].get("headers") or {})
    if "headers" in data["contract"]:
        data["contract"]["headers"] = mask(data["contract"]["headers"])
    for tool in data["tools"]:
        if "operations_headers" in tool:
            tool["operations_headers"] = {
                op: mask(headers) for op, headers in tool["operations_headers"].items()
            }
    return data
