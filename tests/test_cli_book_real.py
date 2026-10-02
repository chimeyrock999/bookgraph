from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app


def _failure_text(result: object) -> str:
    return f"{getattr(result, 'output', '') or ''}\n{getattr(result, 'exception', '') or ''}"


def _error_text(result: object) -> str:
    """The failure text with rich's box drawing and line wrapping taken out."""

    text = _failure_text(result)
    for char in "│╭╮╰╯─":
        text = text.replace(char, " ")
    return " ".join(text.split())


def _fake_mineru_bin(bin_dir: Path) -> Path:
    """A stand-in ``mineru-kit`` that writes a MinerU 4 zip bundle."""

    script = bin_dir / "fake-mineru-kit"
    script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

argv = sys.argv[1:]
assert argv[0] == 'parse', argv
out = Path(argv[argv.index('--output') + 1])
assert argv[argv.index('--format') + 1] == 'zip'
tier = argv[argv.index('--tier') + 1]
ocr_mode = argv[argv.index('--ocr-mode') + 1] if '--ocr-mode' in argv else None
print(f'fake mineru-kit progress tier={tier} ocr_mode={ocr_mode}')

def span(text):
    return [{'type': 'text', 'content': text}]

middle = {
    'schema': 'docvortex.middle',
    'schema_version': '2.0',
    'metadata': {'file_suffix': 'pdf', 'producer': {'name': 'mineru', 'version': '4.0.10'}},
    'extensions': {'mineru': {'tier': tier, 'parse_mode': ocr_mode}},
    'pages': [{
        'page_idx': 0,
        'blocks': [
            {'type': 'header', 'index': 0, 'content': span('Running head')},
            {'type': 'doc_title', 'index': 1, 'level': 1, 'content': span('Deep Work')},
            {'type': 'text', 'index': 2, 'content': span(f'tier={tier} ocr_mode={ocr_mode}')},
            {'type': 'image', 'index': 3, 'content': [
                {'type': 'image_body', 'index': 3, 'image_path': 'images/fig.jpg', 'content': ''},
                {'type': 'image_caption', 'index': 4, 'content': span('Figure 1')},
            ]},
        ],
    }],
    'is_full_document': True,
}
out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, 'w') as bundle:
    bundle.writestr('middle_json.json', json.dumps(middle))
    bundle.writestr('markdown.md', '# Deep Work\\n')
    bundle.writestr('structured_content.json', '{}')
    bundle.writestr('model_output.json', '{}')
    bundle.writestr('images/fig.jpg', b'jpg')
"""
    )
    script.chmod(0o755)
    return script


def _workspace_with_book(tmp_path: Path, runner: CliRunner) -> Path:
    workspace = tmp_path / "workspace"
    pdf = tmp_path / "Deep Work.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert runner.invoke(app, ["add-book", str(workspace), str(pdf)]).exit_code == 0
    return workspace


def _set_mineru_config(workspace: Path, lines: str) -> None:
    config = workspace / "bookgraph.toml"
    config.write_text(config.read_text().replace('profile = "balanced"', lines, 1))


def _argv_line(workspace: Path) -> str:
    return next(line for line in _parse_book_log(workspace).splitlines() if line.startswith("$ "))


def test_parse_book_runs_mineru_then_parser_and_writes_document(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)
    workspace = _workspace_with_book(tmp_path, runner)

    result = runner.invoke(
        app,
        [
            "parse-book",
            str(workspace),
            "deep-work",
            "--runner-command",
            str(fake_mineru),
            "--tier",
            "flash",
            "--ocr-mode",
            "txt",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed_dir = workspace / "sources" / "parsed" / "deep-work"
    document_path = parsed_dir / "document.json"
    document = json.loads(document_path.read_text())
    assert document["doc_id"] == "deep-work"
    assert document["title"] == "Deep Work"
    assert document["metadata"] == {
        "parser": "mineru-middle-json",
        "source_path": str(parsed_dir / "deep-work_middle.json"),
        "mineru_schema": "docvortex.middle/2.0",
        "mineru_version": "4.0.10",
        "mineru_tier": "flash",
        "mineru_file_suffix": "pdf",
        "runner": "mineru",
        "runner_command": str(fake_mineru),
        "runner_profile": "balanced",
    }
    # The running header is dropped; the figure keeps its staged asset.
    assert [block["text"] for block in document["blocks"]] == [
        "Deep Work",
        "tier=flash ocr_mode=txt",
        "Figure 1",
    ]
    assert document["blocks"][2]["asset_path"] == "images/fig.jpg"
    assert (parsed_dir / "images" / "fig.jpg").is_file()
    for name in (
        "deep-work_middle.json",
        "deep-work.md",
        "deep-work_structured_content.json",
        "deep-work_model_output.json",
    ):
        assert (parsed_dir / name).is_file(), name
    assert not (parsed_dir / "_mineru").exists()
    assert not (workspace / "runs" / "cli-placeholders" / "parse-book-deep-work.json").exists()
    assert "runner: mineru" in result.output
    assert "book_id: deep-work" in result.output
    assert "log: " in result.output
    assert "stage: running MinerU" in result.output
    assert "parser: mineru-middle-json" in result.output
    assert f"document: {document_path}" in result.output
    log = _parse_book_log(workspace)
    assert f"runner_command: {fake_mineru}" in log
    assert "tier: flash" in log
    assert "ocr_mode: txt" in log
    assert "fake mineru-kit progress tier=flash ocr_mode=txt" in log
    assert "document.json: exists" in log
    assert "deep-work_middle.json: exists" in log
    assert "deep-work_structured_content.json: exists" in log
    assert "sections/index/plan: not touched by parse-book" in log


def test_parse_book_uses_mineru_config_defaults(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)
    workspace = _workspace_with_book(tmp_path, runner)
    _set_mineru_config(
        workspace,
        f'profile = "balanced"\ncommand = "{fake_mineru}"\ntier = "advanced"\nocr_mode = "ocr"',
    )

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work"])

    assert result.exit_code == 0, result.output
    document_path = workspace / "sources" / "parsed" / "deep-work" / "document.json"
    document = json.loads(document_path.read_text())
    assert document["blocks"][1]["text"] == "tier=advanced ocr_mode=ocr"


def test_parse_book_reads_mineru_3_method_key_as_ocr_mode(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)
    workspace = _workspace_with_book(tmp_path, runner)
    _set_mineru_config(
        workspace, f'profile = "balanced"\ncommand = "{fake_mineru}"\nmethod = "ocr"'
    )

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "--ocr-mode ocr" in _argv_line(workspace)


def test_parse_book_default_profile_runs_the_basic_tier(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)
    workspace = _workspace_with_book(tmp_path, runner)

    result = runner.invoke(
        app, ["parse-book", str(workspace), "deep-work", "--runner-command", str(fake_mineru)]
    )

    assert result.exit_code == 0, result.output
    argv_line = _argv_line(workspace)
    assert f"$ {fake_mineru} parse " in argv_line
    assert "--format zip" in argv_line
    assert "--tier basic" in argv_line
    assert "--ocr-mode auto" in argv_line


def test_parse_book_fast_text_profile_builds_expected_argv(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)
    workspace = _workspace_with_book(tmp_path, runner)

    result = runner.invoke(
        app,
        [
            "parse-book",
            str(workspace),
            "deep-work",
            "--runner-command",
            str(fake_mineru),
            "--profile",
            "fast-text",
            "--start-page",
            "0",
            "--end-page",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "profile: fast-text" in result.output
    log = _parse_book_log(workspace)
    assert "profile: fast-text" in log
    # The exact resolved argv is recorded on the streamed command line.
    argv_line = _argv_line(workspace)
    for fragment in ("--tier flash", "--ocr-mode txt", "--disable-image-analysis", "--pages 1-1"):
        assert fragment in argv_line


def test_parse_book_profile_defaults_come_from_config(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)
    workspace = _workspace_with_book(tmp_path, runner)
    _set_mineru_config(workspace, f'profile = "fast-text"\ncommand = "{fake_mineru}"')

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "profile: fast-text" in result.output
    argv_line = _argv_line(workspace)
    assert "--tier flash" in argv_line
    assert "--ocr-mode txt" in argv_line


def test_parse_book_cli_flag_overrides_config_profile(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)
    workspace = _workspace_with_book(tmp_path, runner)
    _set_mineru_config(workspace, f'profile = "fast-text"\ncommand = "{fake_mineru}"')

    # The 3.x ``--method`` spelling still works as an alias of ``--ocr-mode``.
    result = runner.invoke(
        app, ["parse-book", str(workspace), "deep-work", "--method", "ocr", "--tier", "basic"]
    )

    assert result.exit_code == 0, result.output
    argv_line = _argv_line(workspace)
    assert "--ocr-mode ocr" in argv_line
    assert "--tier basic" in argv_line


def test_parse_book_remote_profile_passes_the_v1_service_url(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)
    workspace = _workspace_with_book(tmp_path, runner)

    result = runner.invoke(
        app,
        [
            "parse-book",
            str(workspace),
            "deep-work",
            "--runner-command",
            str(fake_mineru),
            "--profile",
            "remote-gpu",
            "--url",
            "http://gpu-box:8000",
        ],
    )

    assert result.exit_code == 0, result.output
    argv_line = _argv_line(workspace)
    assert "--remote-url http://gpu-box:8000" in argv_line
    assert "--tier standard" in argv_line
    assert "--ocr-mode" not in argv_line


def test_parse_book_remote_url_refuses_an_ocr_mode_it_would_ignore(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = _workspace_with_book(tmp_path, runner)

    result = runner.invoke(
        app,
        [
            "parse-book",
            str(workspace),
            "deep-work",
            "--profile",
            "remote-gpu",
            "--url",
            "http://gpu-box:8000",
            "--ocr-mode",
            "ocr",
        ],
    )

    assert result.exit_code != 0
    assert "only takes the tier and page range" in _error_text(result)
    assert not (workspace / "sources" / "parsed" / "deep-work").exists()


@pytest.mark.parametrize("url_args", [[], ["--url", ""]])
def test_parse_book_remote_profile_requires_url(tmp_path: Path, url_args: list[str]) -> None:
    runner = CliRunner()
    workspace = _workspace_with_book(tmp_path, runner)

    # An empty --url is normalized to unset, so the guard still fires.
    result = runner.invoke(
        app,
        ["parse-book", str(workspace), "deep-work", "--profile", "remote-gpu", *url_args],
    )

    assert result.exit_code != 0
    assert "needs its URL" in _failure_text(result)
    assert not (workspace / "sources" / "parsed" / "deep-work").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--backend", "pipeline"], "backend 'pipeline' was removed in MinerU 4. Use --tier basic"),
        (["--backend", "hybrid-http-client"], "with --url for a remote MinerU V1 service"),
        (["--effort", "high"], "effort 'high' was removed"),
        (["--no-table"], "table toggle was removed"),
        (["--formula"], "formula toggle was removed"),
    ],
)
def test_parse_book_refuses_removed_mineru_3_flags(
    tmp_path: Path, args: list[str], message: str
) -> None:
    runner = CliRunner()
    workspace = _workspace_with_book(tmp_path, runner)

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work", *args])

    assert result.exit_code != 0
    assert message in _error_text(result)
    assert not (workspace / "sources" / "parsed" / "deep-work").exists()


def test_parse_book_refuses_removed_mineru_3_config_keys(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = _workspace_with_book(tmp_path, runner)
    _set_mineru_config(workspace, 'profile = "balanced"\nbackend = "vlm-engine"')

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work"])

    assert result.exit_code != 0
    assert "Use --tier standard" in _error_text(result)


def test_parse_book_points_a_mineru_3_command_at_mineru_kit(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = _workspace_with_book(tmp_path, runner)
    _set_mineru_config(workspace, 'profile = "balanced"\ncommand = "mineru"')

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work"])

    assert result.exit_code != 0
    assert "MinerU 4 parses with 'mineru-kit'" in _error_text(result)


def test_parse_book_removed_flags_are_hidden_from_help() -> None:
    result = CliRunner().invoke(app, ["parse-book", "--help"])

    assert result.exit_code == 0
    assert "--tier" in result.output
    assert "--ocr-mode" in result.output
    for removed in ("--backend", "--effort", "--formula", "--no-table"):
        assert removed not in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--ocr-mode", "txxt"], "Unknown MinerU OCR mode 'txxt'"),
        (["--tier", "turbo"], "Unknown MinerU tier 'turbo'"),
    ],
)
def test_parse_book_rejects_unknown_tier_and_ocr_mode(
    tmp_path: Path, args: list[str], message: str
) -> None:
    runner = CliRunner()
    workspace = _workspace_with_book(tmp_path, runner)

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work", *args])

    assert result.exit_code != 0
    assert message in _failure_text(result)


def test_parse_book_failure_reports_log_path(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    pdf = tmp_path / "Deep Work.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    failing_mineru = bin_dir / "failing-mineru"
    failing_mineru.write_text(
        """#!/usr/bin/env python3
print('starting fake mineru')
raise SystemExit(7)
"""
    )
    failing_mineru.chmod(0o755)

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert runner.invoke(app, ["add-book", str(workspace), str(pdf)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "parse-book",
            str(workspace),
            "deep-work",
            "--runner-command",
            str(failing_mineru),
        ],
    )

    assert result.exit_code != 0
    assert "Log:" in result.output
    assert "starting fake mineru" in result.output
    logs = list((workspace / "runs" / "parse-book").glob("*-deep-work.log"))
    assert len(logs) == 1
    log = logs[0].read_text()
    assert "starting fake mineru" in log
    assert "[bookgraph] process exit code: 7" in log
    assert "document.json: missing" in log
    assert "deep-work_middle.json: missing" in log


def test_parse_book_requires_registered_original_source(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["parse-book", str(workspace), "missing-book"])

    assert result.exit_code != 0
    assert "Registered original source not found" in result.output
    assert not (workspace / "sources" / "parsed" / "missing-book").exists()


def _parse_book_log(workspace: Path) -> str:
    logs = list((workspace / "runs" / "parse-book").glob("*-deep-work.log"))
    assert len(logs) == 1
    return logs[0].read_text()


def test_parse_book_rejects_unknown_profile(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    pdf = tmp_path / "Deep Work.pdf"
    pdf.write_bytes(b"%PDF-1.7")

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert runner.invoke(app, ["add-book", str(workspace), str(pdf)]).exit_code == 0

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work", "--profile", "turbo"])

    assert result.exit_code != 0
    assert "Unknown MinerU profile: turbo" in _failure_text(result)


def test_parse_book_dry_run_keeps_placeholder_contract(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert (workspace / "runs" / "cli-placeholders" / "parse-book-deep-work.json").is_file()
    assert not (workspace / "sources" / "parsed" / "deep-work").exists()
    assert "Backend not run" in result.output
