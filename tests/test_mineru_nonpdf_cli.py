"""``MinerURunner`` and ``parse-book`` on a registered EPUB/DOCX (MinerU's flash-only path)."""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.parsers.mineru_runner import MinerURunner

FIXTURES = Path(__file__).parent / "fixtures" / "mineru4"


def _write_bundle(kind: str, target: Path) -> None:
    recorded = FIXTURES / kind
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as bundle:
        bundle.write(recorded / "middle_json.json", "middle_json.json")
        bundle.write(recorded / "markdown.md", "markdown.md")
        for image in (recorded / "images").iterdir():
            bundle.write(image, f"images/{image.name}")


def _recording_mineru(kind: str, calls: list[list[str]]):
    def run_process(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        _write_bundle(kind, Path(argv[argv.index("--output") + 1]))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return run_process


@pytest.mark.parametrize("kind", ["epub", "docx"])
def test_runner_forces_flash_and_stages_a_source_map(tmp_path: Path, kind: str) -> None:
    calls: list[list[str]] = []
    out = tmp_path / "parsed" / "sample"
    # The default profile resolves to ``basic``, which MinerU rejects for these formats.
    runner = MinerURunner(tier="basic", run_process=_recording_mineru(kind, calls))

    result = runner.run(FIXTURES / kind / f"sample.{kind}", out)

    (argv,) = calls
    assert argv[argv.index("--tier") + 1] == "flash"
    assert "--pages" not in argv
    assert result.source_map == out / "sample_source_map.json"
    assert json.loads(result.source_map.read_text())["source"] == f"sample.{kind}"
    assert result.artifacts()["source_map"] == result.source_map


def test_runner_keeps_the_tier_and_writes_no_source_map_for_pdf(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    out = tmp_path / "parsed" / "sample"
    out.mkdir(parents=True)
    # A stale map from an earlier EPUB run of the same doc id must not be read.
    (out / "sample_source_map.json").write_text("{}")

    result = MinerURunner(tier="basic", run_process=_recording_mineru("epub", calls)).run(pdf, out)

    assert calls[0][calls[0].index("--tier") + 1] == "basic"
    assert result.source_map is None
    assert not (out / "sample_source_map.json").exists()


@pytest.mark.parametrize(("start", "end"), [(0, None), (None, 3), (1, 2)])
def test_runner_rejects_a_page_range_for_non_pdf(
    tmp_path: Path, start: int | None, end: int | None
) -> None:
    calls: list[list[str]] = []
    runner = MinerURunner(
        start_page=start, end_page=end, run_process=_recording_mineru("epub", calls)
    )

    with pytest.raises(UnsupportedSourceError, match="page range"):
        runner.run(FIXTURES / "epub" / "sample.epub", tmp_path / "parsed" / "sample")
    assert calls == []


def _fake_mineru_bin(bin_dir: Path) -> Path:
    """A stand-in ``mineru-kit`` that answers like MinerU 4 for flash-only inputs."""

    script = bin_dir / "fake-mineru-kit"
    script.write_text(
        f"""#!/usr/bin/env python3
import sys, zipfile
from pathlib import Path

argv = sys.argv[1:]
source = Path(argv[1])
kind = source.suffix.lstrip('.')
if argv[argv.index('--tier') + 1] != 'flash' or '--pages' in argv:
    print('Error: tier/page range not supported for ' + kind, file=sys.stderr)
    sys.exit(1)
recorded = Path({str(FIXTURES)!r}) / kind
out = Path(argv[argv.index('--output') + 1])
out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, 'w') as bundle:
    bundle.write(recorded / 'middle_json.json', 'middle_json.json')
    bundle.write(recorded / 'markdown.md', 'markdown.md')
    for image in (recorded / 'images').iterdir():
        bundle.write(image, 'images/' + image.name)
"""
    )
    script.chmod(0o755)
    return script


def _workspace_with(tmp_path: Path, runner: CliRunner, kind: str) -> Path:
    workspace = tmp_path / "workspace"
    source = tmp_path / f"Sample.{kind}"
    source.write_bytes((FIXTURES / kind / f"sample.{kind}").read_bytes())
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    result = runner.invoke(app, ["add-book", str(workspace), str(source)])
    assert result.exit_code == 0, result.output
    return workspace


def _error_text(result: object) -> str:
    text = f"{getattr(result, 'output', '') or ''}"
    for char in "│╭╮╰╯─":
        text = text.replace(char, " ")
    return " ".join(text.split())


@pytest.mark.parametrize("kind", ["epub", "docx"])
def test_add_book_registers_epub_and_docx(tmp_path: Path, kind: str) -> None:
    workspace = _workspace_with(tmp_path, CliRunner(), kind)

    book_root = workspace / "sources" / "inbox" / "sample"
    manifest = json.loads((book_root / "book.json").read_text())
    assert manifest["source_type"] == kind
    assert manifest["paths"]["original"] == str(book_root / f"original.{kind}")
    assert (book_root / f"original.{kind}").is_file()


def test_parse_book_runs_the_explicit_mineru_path_on_an_epub(tmp_path: Path) -> None:
    runner = CliRunner()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    workspace = _workspace_with(tmp_path, runner, "epub")
    # A PDF-oriented tier in the config gives way to flash for an EPUB.
    config = workspace / "bookgraph.toml"
    config.write_text(
        config.read_text().replace('profile = "balanced"', 'profile = "balanced"\ntier = "basic"')
    )

    result = runner.invoke(
        app,
        [
            "parse-book",
            str(workspace),
            "sample",
            "--runner-command",
            str(_fake_mineru_bin(bin_dir)),
        ],
    )

    assert result.exit_code == 0, result.output
    parsed_dir = workspace / "sources" / "parsed" / "sample"
    document = json.loads((parsed_dir / "document.json").read_text())
    assert document["metadata"]["mineru_file_suffix"] == "epub"
    assert document["metadata"]["source_name"] == "original.epub"
    block = next(b for b in document["blocks"] if b["id"] == "p1.b1")
    assert block["metadata"]["source_locator"] == "original.epub!OEBPS/text/ch02.xhtml"
    assert block["metadata"]["anchor"] == "epub-a652b1f1fec799e1a325"
    assert (parsed_dir / "sample_source_map.json").is_file()
    log = next((workspace / "runs" / "parse-book").glob("*-sample.log")).read_text()
    assert "tier: flash" in log


def test_parse_book_dry_run_reports_flash_and_the_source_map(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = _workspace_with(tmp_path, runner, "docx")

    result = runner.invoke(app, ["parse-book", str(workspace), "sample", "--dry-run"])

    assert result.exit_code == 0, result.output
    payload = json.loads(
        (workspace / "runs" / "cli-placeholders" / "parse-book-sample.json").read_text()
    )
    assert payload["runner"]["tier"] == "flash"
    assert payload["inputs"]["original_source"].endswith("original.docx")
    assert payload["intermediate_outputs"]["source_map"].endswith("sample_source_map.json")


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--tier", "basic"], "with --tier flash only"),
        (["--start-page", "0"], "apply to PDFs only"),
        (["--end-page", "2"], "apply to PDFs only"),
    ],
)
def test_parse_book_rejects_pdf_only_options_for_docx(
    tmp_path: Path, flags: list[str], message: str
) -> None:
    runner = CliRunner()
    workspace = _workspace_with(tmp_path, runner, "docx")

    result = runner.invoke(app, ["parse-book", str(workspace), "sample", "--dry-run", *flags])

    assert result.exit_code != 0
    assert message in _error_text(result)


def test_parse_book_accepts_an_explicit_flash_tier_for_docx(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = _workspace_with(tmp_path, runner, "docx")

    result = runner.invoke(
        app, ["parse-book", str(workspace), "sample", "--dry-run", "--tier", "flash"]
    )

    assert result.exit_code == 0, result.output
