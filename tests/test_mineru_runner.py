from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.parsers.mineru_runner import (
    MinerUNotInstalledError,
    MinerURunError,
    MinerURunner,
    _stream_run_process,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mineru4"


def _output_zip_from_argv(argv: list[str]) -> Path:
    return Path(argv[argv.index("--output") + 1])


def write_result_zip(target: Path, *, siblings: bool = True, middle: bool = True) -> None:
    """Write a ``mineru-kit parse --format zip`` bundle from the recorded fixture."""

    target.parent.mkdir(parents=True, exist_ok=True)
    recorded = FIXTURES / "basic"
    with zipfile.ZipFile(target, "w") as bundle:
        if middle:
            bundle.write(recorded / "middle_json.json", "middle_json.json")
        if siblings:
            bundle.write(recorded / "markdown.md", "markdown.md")
            bundle.write(recorded / "structured_content.json", "structured_content.json")
            bundle.writestr("model_output.json", json.dumps({"schema": "docvortex.model"}))
            bundle.writestr("images/page_1_image_4.jpg", b"jpg")


def _fake_mineru(
    *,
    returncode: int = 0,
    stderr: str = "",
    produce_bundle: bool = True,
    siblings: bool = True,
):
    """Build a run_process seam that mimics ``mineru-kit`` writing its zip bundle."""

    def run_process(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if returncode == 0 and produce_bundle:
            write_result_zip(_output_zip_from_argv(argv), siblings=siblings)
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr=stderr)

    return run_process


def _pdf(tmp_path: Path, name: str = "book.pdf") -> Path:
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.7")
    return pdf


def test_run_stages_middle_json_and_siblings_flat(tmp_path: Path) -> None:
    out = tmp_path / "parsed" / "ddia"
    runner = MinerURunner(run_process=_fake_mineru())

    result = runner.run(_pdf(tmp_path), out)

    assert result.middle_json == out / "ddia_middle.json"
    assert json.loads(result.middle_json.read_text())["schema"] == "docvortex.middle"
    assert result.markdown == out / "ddia.md"
    assert result.structured_content == out / "ddia_structured_content.json"
    assert result.model_output == out / "ddia_model_output.json"
    assert result.images_dir == out / "images"
    # Markdown and middle JSON reference ``images/<file>``, so the dir keeps its name.
    assert (out / "images" / "page_1_image_4.jpg").is_file()
    assert "images/page_1_image_4.jpg" in result.markdown.read_text()
    # The temporary MinerU work dir is cleaned up after staging.
    assert not (out / "_mineru").exists()


def _capture_argv(tmp_path: Path, **runner_kwargs: object) -> list[str]:
    captured: list[list[str]] = []

    def spy(argv: list[str]) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        return _fake_mineru()(argv)

    runner = MinerURunner(run_process=spy, **runner_kwargs)  # type: ignore[arg-type]
    runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")
    return captured[0]


def test_run_builds_mineru_kit_zip_argv(tmp_path: Path) -> None:
    argv = _capture_argv(tmp_path, tier="flash", ocr_mode="txt")

    assert argv[:3] == ["mineru-kit", "parse", str(_pdf(tmp_path))]
    assert argv[argv.index("--format") + 1] == "zip"
    assert argv[argv.index("--tier") + 1] == "flash"
    assert argv[argv.index("--ocr-mode") + 1] == "txt"
    assert _output_zip_from_argv(argv).parent == tmp_path / "parsed" / "doc" / "_mineru"


def test_argv_disables_image_analysis_only_when_false(tmp_path: Path) -> None:
    assert "--disable-image-analysis" in _capture_argv(tmp_path, image_analysis=False)
    assert "--disable-image-analysis" not in _capture_argv(tmp_path, image_analysis=True)
    assert "--disable-image-analysis" not in _capture_argv(tmp_path, image_analysis=None)


def test_argv_remote_service_passes_url_but_never_an_api_key(tmp_path: Path) -> None:
    argv = _capture_argv(tmp_path, tier="standard", url="http://gpu-box:8000")

    assert argv[argv.index("--remote-url") + 1] == "http://gpu-box:8000"
    # MinerU reads the key from MINERU_API_KEY, so it never reaches argv or the run log.
    assert "--api-key" not in argv


def test_argv_remote_service_omits_local_only_knobs(tmp_path: Path) -> None:
    argv = _capture_argv(tmp_path, url="http://gpu-box:8000", ocr_mode="ocr", image_analysis=False)

    # mineru-kit ignores these on the remote branch, so they are not claimed in argv.
    assert "--ocr-mode" not in argv
    assert "--disable-image-analysis" not in argv


@pytest.mark.parametrize(
    ("start_page", "end_page", "pages"),
    [(2, 9, "3-10"), (0, 0, "1-1"), (4, None, "5-r1"), (None, 6, "1-7")],
)
def test_argv_maps_zero_based_bounds_to_one_based_pages(
    tmp_path: Path, start_page: int | None, end_page: int | None, pages: str
) -> None:
    argv = _capture_argv(tmp_path, start_page=start_page, end_page=end_page)

    assert argv[argv.index("--pages") + 1] == pages


def test_argv_omits_unset_optional_knobs(tmp_path: Path) -> None:
    argv = _capture_argv(tmp_path)

    assert argv[argv.index("--tier") + 1] == "basic"
    assert argv[argv.index("--ocr-mode") + 1] == "auto"
    for flag in ("--disable-image-analysis", "--remote-url", "--pages", "--api-key"):
        assert flag not in argv


def test_run_only_stages_artifacts_mineru_produced(tmp_path: Path) -> None:
    runner = MinerURunner(run_process=_fake_mineru(siblings=False))

    result = runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")

    assert result.middle_json.is_file()
    assert result.markdown is None
    assert result.images_dir is None
    assert set(result.artifacts()) == {"middle_json"}


def test_run_points_a_mineru_3_command_at_mineru_kit(tmp_path: Path) -> None:
    runner = MinerURunner(command="mineru", run_process=_fake_mineru())

    with pytest.raises(MinerURunError, match="MinerU 4 parses with 'mineru-kit'"):
        runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")


def test_restaging_removes_mineru_3_side_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "parsed" / "doc"
    out.mkdir(parents=True)
    legacy = [out / f"doc{suffix}" for suffix in ("_layout.pdf", "_span.pdf", "_content_list.json")]
    for path in legacy:
        path.write_bytes(b"3.x")
    unrelated = out / "notes.txt"
    unrelated.write_text("keep")

    MinerURunner(run_process=_fake_mineru()).run(_pdf(tmp_path), out)

    assert not any(path.exists() for path in legacy)
    assert unrelated.read_text() == "keep"


def test_failed_run_keeps_mineru_3_side_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "parsed" / "doc"
    out.mkdir(parents=True)
    layout = out / "doc_layout.pdf"
    layout.write_bytes(b"3.x")

    with pytest.raises(MinerURunError):
        MinerURunner(run_process=_fake_mineru(returncode=1)).run(_pdf(tmp_path), out)

    assert layout.is_file()


def test_run_rejects_non_pdf_input(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("hi")
    runner = MinerURunner(run_process=_fake_mineru())

    with pytest.raises(UnsupportedSourceError, match="accepts a raw .pdf"):
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


def test_run_raises_when_no_bundle_is_produced(tmp_path: Path) -> None:
    runner = MinerURunner(run_process=_fake_mineru(produce_bundle=False))

    with pytest.raises(MinerURunError, match="no result bundle"):
        runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")


def test_run_raises_when_bundle_has_no_middle_json(tmp_path: Path) -> None:
    def no_middle(argv: list[str]) -> subprocess.CompletedProcess[str]:
        write_result_zip(_output_zip_from_argv(argv), middle=False)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = MinerURunner(run_process=no_middle)
    out = tmp_path / "parsed" / "doc"

    with pytest.raises(MinerURunError, match="has no middle_json.json"):
        runner.run(_pdf(tmp_path), out)
    assert not (out / "doc_middle.json").exists()


def test_run_rejects_a_bundle_that_is_not_a_zip(tmp_path: Path) -> None:
    def not_zip(argv: list[str]) -> subprocess.CompletedProcess[str]:
        _output_zip_from_argv(argv).write_text("# markdown, not a zip")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = MinerURunner(run_process=not_zip)

    with pytest.raises(MinerURunError, match="not a zip"):
        runner.run(_pdf(tmp_path), tmp_path / "parsed" / "doc")


def test_run_maps_timeout_to_run_error(tmp_path: Path) -> None:
    def timing_out(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    runner = MinerURunner(timeout_seconds=1, run_process=timing_out)
    out = tmp_path / "parsed" / "doc"

    with pytest.raises(MinerURunError, match="timed out"):
        runner.run(_pdf(tmp_path), out)
    assert not (out / "_mineru").exists()


def test_run_keeps_zip_members_inside_the_work_dir(tmp_path: Path) -> None:
    def escaping(argv: list[str]) -> subprocess.CompletedProcess[str]:
        target = _output_zip_from_argv(argv)
        write_result_zip(target)
        with zipfile.ZipFile(target, "a") as bundle:
            bundle.writestr("../../escaped.txt", "x")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = MinerURunner(run_process=escaping)
    out = tmp_path / "parsed" / "doc"
    runner.run(_pdf(tmp_path), out)

    assert not (tmp_path / "parsed" / "escaped.txt").exists()
    assert not (out / "escaped.txt").exists()


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
