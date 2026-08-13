from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.parsers.mineru_runner import (
    MinerUNotInstalledError,
    MinerURunError,
    MinerURunner,
    _stream_run_process,
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


def test_run_reports_merged_stream_output_on_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "failing-mineru"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('mineru exploded\\n')\n"
        "raise SystemExit(9)\n"
    )
    script.chmod(0o755)
    runner = MinerURunner(command=str(script), run_process=None, log_path=tmp_path / "mineru.log")
    out = tmp_path / "parsed" / "doc"

    with pytest.raises(MinerURunError, match="mineru exploded"):
        runner.run(_pdf(tmp_path), out)

    assert "mineru exploded" in (tmp_path / "mineru.log").read_text()
    assert not (out / "_mineru").exists()


def test_stream_run_process_streams_carriage_return_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = (
        "import sys\n"
        "sys.stderr.write('download 10%\\r')\n"
        "sys.stderr.flush()\n"
        "sys.stderr.write('download 20%\\n')\n"
        "sys.stderr.flush()\n"
    )

    completed = _stream_run_process(
        [sys.executable, "-c", code], timeout=10, log_path=tmp_path / "stream.log"
    )

    assert completed.returncode == 0
    assert "download 10%\rdownload 20%\n" in completed.stdout
    assert completed.stderr == completed.stdout
    log_text = (tmp_path / "stream.log").open(newline="").read()
    assert "download 10%\rdownload 20%" in log_text
    assert "download 10%\rdownload 20%" in capsys.readouterr().err


def test_run_raises_when_no_middle_json_is_produced(tmp_path: Path) -> None:
    runner = MinerURunner(run_process=_fake_mineru(produce_middle=False))

    with pytest.raises(MinerURunError, match="no .*_middle.json"):
        runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")


def test_run_maps_timeout_to_run_error(tmp_path: Path) -> None:
    def timing_out(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    runner = MinerURunner(timeout_seconds=1, run_process=timing_out)
    out = tmp_path / "parsed" / "doc"

    with pytest.raises(MinerURunError, match="timed out"):
        runner.run(_pdf(tmp_path), out)
    assert not (out / "_mineru").exists()


def test_run_refuses_multiple_middle_json(tmp_path: Path) -> None:
    def two_middle(argv: list[str]) -> subprocess.CompletedProcess[str]:
        work = _output_dir_from_argv(argv)
        for name in ("a", "b"):
            auto = work / name / "auto"
            auto.mkdir(parents=True, exist_ok=True)
            (auto / f"{name}_middle.json").write_text('{"pdf_info": []}')
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = MinerURunner(run_process=two_middle)

    with pytest.raises(MinerURunError, match="multiple"):
        runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")


def test_run_rolls_back_partial_staging_on_copy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bookgraph.parsers.mineru_runner as mod

    def failing_copytree(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mod.shutil, "copytree", failing_copytree)
    runner = MinerURunner(run_process=_fake_mineru())  # produces images/ -> copytree
    out = tmp_path / "parsed" / "doc"

    with pytest.raises(OSError, match="disk full"):
        runner.run(_pdf(tmp_path), out)

    # Nothing half-staged and the temp work dir is gone.
    assert not (out / "doc_middle.json").exists()
    assert not (out / "doc.md").exists()
    assert not (out / "_mineru").exists()
