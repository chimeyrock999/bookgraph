from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.parsers.mineru_runner import (
    MinerUNotInstalledError,
    MinerURunError,
    MinerURunner,
)


def _output_dir_from_argv(argv: list[str]) -> Path:
    return Path(argv[argv.index("-o") + 1])


def _fake_mineru(
    *,
    stem: str = "book",
    returncode: int = 0,
    stderr: str = "",
    produce_middle: bool = True,
    siblings: bool = True,
):
    """Build a run_process seam that mimics MinerU writing its nested output."""

    def run_process(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if returncode == 0 and produce_middle:
            auto_dir = _output_dir_from_argv(argv) / stem / "auto"
            auto_dir.mkdir(parents=True, exist_ok=True)
            (auto_dir / f"{stem}_middle.json").write_text('{"pdf_info": []}')
            if siblings:
                (auto_dir / f"{stem}.md").write_text("# Book\n")
                (auto_dir / f"{stem}_layout.pdf").write_bytes(b"%PDF-layout")
                (auto_dir / f"{stem}_span.pdf").write_bytes(b"%PDF-span")
                (auto_dir / f"{stem}_content_list.json").write_text("[]")
                images = auto_dir / "images"
                images.mkdir(exist_ok=True)
                (images / "fig1.png").write_bytes(b"png")
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr=stderr)

    return run_process


def _pdf(tmp_path: Path, name: str = "book.pdf") -> Path:
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.7")
    return pdf


def test_run_stages_middle_json_and_siblings_flat(tmp_path: Path) -> None:
    out = tmp_path / "parsed" / "ddia"
    runner = MinerURunner(run_process=_fake_mineru(stem="book"))

    result = runner.run(_pdf(tmp_path), out)

    assert result.middle_json == out / "ddia_middle.json"
    assert result.middle_json.is_file()
    assert result.markdown == out / "ddia.md"
    assert result.layout_pdf == out / "ddia_layout.pdf"
    assert result.span_pdf == out / "ddia_span.pdf"
    assert result.content_list == out / "ddia_content_list.json"
    assert result.images_dir == out / "images"
    assert (out / "images" / "fig1.png").is_file()
    # The temporary MinerU work dir is cleaned up after staging.
    assert not (out / "_mineru").exists()


def test_run_builds_expected_argv(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def spy(argv: list[str]) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        return _fake_mineru()(argv)

    runner = MinerURunner(command="mineru", method="ocr", backend="pipeline", run_process=spy)
    runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")

    argv = captured[0]
    assert argv[0] == "mineru"
    assert argv[argv.index("-p") + 1] == str(_pdf(tmp_path))
    assert argv[argv.index("-m") + 1] == "ocr"
    assert argv[argv.index("-b") + 1] == "pipeline"


def test_run_only_stages_artifacts_mineru_produced(tmp_path: Path) -> None:
    runner = MinerURunner(run_process=_fake_mineru(siblings=False))

    result = runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")

    assert result.middle_json.is_file()
    assert result.markdown is None
    assert result.images_dir is None
    assert set(result.artifacts()) == {"middle_json"}


def test_run_rejects_non_pdf_input(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("hi")
    runner = MinerURunner(run_process=_fake_mineru())

    with pytest.raises(UnsupportedSourceError, match="only accepts raw .pdf"):
        runner.run(source, tmp_path / "parsed" / "doc")


def test_run_reports_a_missing_pdf(tmp_path: Path) -> None:
    runner = MinerURunner(run_process=_fake_mineru())

    with pytest.raises(UnsupportedSourceError, match="PDF not found"):
        runner.run(tmp_path / "ghost.pdf", tmp_path / "parsed" / "doc")


def test_run_raises_when_executable_is_missing(tmp_path: Path) -> None:
    # No run_process injected => default subprocess path, which requires the binary.
    runner = MinerURunner(command="mineru-does-not-exist-xyz")

    with pytest.raises(MinerUNotInstalledError, match="not found on PATH"):
        runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")


def test_run_raises_on_nonzero_exit(tmp_path: Path) -> None:
    runner = MinerURunner(run_process=_fake_mineru(returncode=2, stderr="boom"))
    out = tmp_path / "parsed" / "doc"

    with pytest.raises(MinerURunError, match="boom"):
        runner.run(_pdf(tmp_path), out)
    assert not (out / "_mineru").exists()


def test_run_raises_when_no_middle_json_is_produced(tmp_path: Path) -> None:
    runner = MinerURunner(run_process=_fake_mineru(produce_middle=False))

    with pytest.raises(MinerURunError, match="no .*_middle.json"):
        runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")
