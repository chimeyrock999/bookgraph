from __future__ import annotations

import codecs
import queue
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.utils import MINERU_MIDDLE_JSON_SUFFIX

CommandRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

DEFAULT_COMMAND = "mineru-kit"
# The MinerU 3.x executable. In MinerU 4 ``mineru`` is the document-library CLI and
# one-off conversion moved to ``mineru-kit``, so this name gets a migration hint.
_LEGACY_COMMAND = "mineru"
_WORK_SUBDIR = "_mineru"
_RESULT_ZIP = "result.zip"
_DEFAULT_TIMEOUT_SECONDS = 3600
_PROCESS_EXIT_GRACE_SECONDS = 10
_ERROR_EXCERPT_CHARS = 4000

# Fixed member names of the ``mineru-kit parse --format zip`` result bundle.
_ZIP_MIDDLE_JSON = "middle_json.json"
_ZIP_MARKDOWN = "markdown.md"
_ZIP_STRUCTURED_CONTENT = "structured_content.json"
_ZIP_MODEL_OUTPUT = "model_output.json"
_ZIP_IMAGES_DIR = "images"


class MinerUNotInstalledError(RuntimeError):
    """Raised when the MinerU executable is not on PATH."""


class MinerURunError(RuntimeError):
    """Raised when MinerU runs but does not produce usable output."""


class _MinerUProcessReapTimeoutError(MinerURunError):
    """Raised when subprocess output ended but its exit status did not arrive."""


@dataclass(frozen=True)
class MinerURunResult:
    """Paths to the MinerU artifacts staged under the document's parsed dir."""

    middle_json: Path
    command: list[str]
    markdown: Path | None = None
    structured_content: Path | None = None
    model_output: Path | None = None
    images_dir: Path | None = None

    def artifacts(self) -> dict[str, Path]:
        """Return staged artifacts keyed by role, skipping the ones MinerU omitted."""

        mapping = {
            "middle_json": self.middle_json,
            "markdown": self.markdown,
            "structured_content": self.structured_content,
            "model_output": self.model_output,
            "images_dir": self.images_dir,
        }
        return {role: path for role, path in mapping.items() if path is not None}


@dataclass
class MinerURunner:
    """Invoke MinerU 4 on a raw PDF and stage its result bundle.

    MinerU is a heavy external tool, so it stays out of the base install and is
    invoked as a subprocess (``mineru-kit parse <pdf> --format zip``) rather than
    imported. ``run_process`` can be injected to exercise the runner without MinerU
    installed; when it is ``None`` the default subprocess runner is used and the
    executable is required on PATH.

    The runner owns only the "invoke the heavy process" step. Turning the staged
    ``*_middle.json`` into a canonical ``document.json`` remains the job of
    :class:`bookgraph.parsers.mineru.MinerUMiddleJsonParser` via ``bookgraph parse``.

    A remote MinerU V1 parse service is used when ``url`` is set; its API key is
    read by MinerU from ``MINERU_API_KEY`` so it never lands in argv or the run log.
    """

    name: str = "mineru"
    command: str = DEFAULT_COMMAND
    tier: str = "basic"
    ocr_mode: str = "auto"
    image_analysis: bool | None = None
    url: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    timeout_seconds: int | None = _DEFAULT_TIMEOUT_SECONDS
    run_process: CommandRunner | None = field(default=None)
    log_path: Path | None = None

    def run(self, pdf: Path, output_dir: Path) -> MinerURunResult:
        if pdf.suffix.lower() != ".pdf":
            raise UnsupportedSourceError(
                f"{pdf.name}: {self.name} runner only accepts raw .pdf input."
            )
        if not pdf.is_file():
            raise UnsupportedSourceError(f"PDF not found: {pdf}")
        if Path(self.command).name == _LEGACY_COMMAND:
            raise MinerURunError(
                f"'{self.command}' is the MinerU 3.x parse command; MinerU 4 parses with "
                f"'{DEFAULT_COMMAND}'. Set [mineru].command = \"{DEFAULT_COMMAND}\" or pass "
                f"--runner-command {DEFAULT_COMMAND}."
            )

        process: CommandRunner | None = self.run_process
        if process is None:
            if shutil.which(self.command) is None:
                raise MinerUNotInstalledError(
                    f"MinerU executable '{self.command}' not found on PATH. "
                    "Install with: uv sync --extra mineru"
                )
            timeout = self.timeout_seconds

            def _run_default(argv: list[str]) -> subprocess.CompletedProcess[str]:
                if self.log_path is not None:
                    return _stream_run_process(argv, timeout, self.log_path)
                return _default_run_process(argv, timeout)

            process = _run_default

        work_dir = output_dir / _WORK_SUBDIR
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        result_zip = work_dir / _RESULT_ZIP
        argv = self._build_argv(pdf, result_zip)
        try:
            try:
                completed = process(argv)
            except subprocess.TimeoutExpired as exc:
                raise MinerURunError(
                    f"MinerU timed out after {self.timeout_seconds}s on {pdf.name}."
                ) from exc
            if completed.returncode != 0:
                raise MinerURunError(
                    f"MinerU failed on {pdf.name} (exit {completed.returncode}): "
                    f"{_process_error_excerpt(completed)}"
                )

            bundle = _extract_result_bundle(result_zip, work_dir / "result", pdf)
            # Staging copies out of work_dir before the finally cleanup removes it.
            return _stage_artifacts(bundle, output_dir, stem=output_dir.name, command=argv)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _build_argv(self, pdf: Path, result_zip: Path) -> list[str]:
        argv = [
            self.command,
            "parse",
            str(pdf),
            "--output",
            str(result_zip),
            "--format",
            "zip",
            "--tier",
            self.tier,
            "--ocr-mode",
            self.ocr_mode,
        ]
        if self.image_analysis is False:
            argv.append("--disable-image-analysis")
        if self.url:
            argv += ["--remote-url", self.url]
        if (pages := _page_range(self.start_page, self.end_page)) is not None:
            argv += ["--pages", pages]
        return argv


def _page_range(start_page: int | None, end_page: int | None) -> str | None:
    """Map BookGraph's 0-based inclusive bounds onto MinerU 4's 1-based ranges.

    MinerU 4 has no open-ended range, so a missing end is ``r1`` (the last page)
    and a missing start is page 1.
    """

    if start_page is None and end_page is None:
        return None
    first = 1 if start_page is None else start_page + 1
    last = "r1" if end_page is None else str(end_page + 1)
    return f"{first}-{last}"


def _default_run_process(
    argv: list[str], timeout: int | None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        argv, capture_output=True, text=True, check=False, timeout=timeout
    )


def _stream_run_process(
    argv: list[str], timeout: int | None, log_path: Path
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess while teeing combined stdout/stderr to terminal and log.

    MinerU can run for a long time on large PDFs and emits useful progress. The
    default CLI path should surface that output immediately while also leaving a
    durable log for agents/cron jobs to inspect. Unit tests can still inject
    ``run_process`` to avoid spawning MinerU.
    """

    log_path.parent.mkdir(parents=True, exist_ok=True)
    output: list[str] = []
    deadline = time.monotonic() + timeout if timeout is not None else None
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"$ {' '.join(argv)}\n")
        log.flush()
        process = subprocess.Popen(  # noqa: S603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        assert process.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(
            target=_enqueue_output,
            args=(process.stdout, lines),
            daemon=True,
        )
        reader.start()
        try:
            while True:
                if deadline is not None and time.monotonic() > deadline:
                    process.kill()
                    assert timeout is not None
                    raise subprocess.TimeoutExpired(argv, timeout, output="".join(output))
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    if process.poll() is not None and not reader.is_alive():
                        break
                    continue
                if line is None:
                    break
                output.append(line)
                log.write(line)
                if line in {"\n", "\r"}:
                    log.flush()
                print(line, end="", file=sys.stderr, flush=line in {"\n", "\r"})
            log.flush()
            try:
                returncode = process.wait(timeout=_PROCESS_EXIT_GRACE_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise _MinerUProcessReapTimeoutError(
                    "MinerU output stream ended, but the process did not exit within "
                    f"{_PROCESS_EXIT_GRACE_SECONDS}s."
                ) from exc
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        except _MinerUProcessReapTimeoutError:
            process.kill()
            process.wait()
            raise
        log.write(f"\n[bookgraph] process exit code: {returncode}\n")
        return subprocess.CompletedProcess(
            argv,
            returncode,
            stdout="".join(output),
            stderr="".join(output),
        )


def _enqueue_output(stream: BinaryIO, lines: queue.Queue[str | None]) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while chunk := stream.read(1):
            text = decoder.decode(chunk)
            if text:
                lines.put(text)
        remainder = decoder.decode(b"", final=True)
        if remainder:
            lines.put(remainder)
    finally:
        lines.put(None)


def _process_error_excerpt(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    if not text:
        return "no subprocess output"
    if len(text) <= _ERROR_EXCERPT_CHARS:
        return text
    return "…" + text[-_ERROR_EXCERPT_CHARS:]


def _extract_result_bundle(result_zip: Path, target: Path, pdf: Path) -> Path:
    """Unpack the ``mineru-kit`` zip bundle and check it holds MinerU 4 middle JSON."""

    if not result_zip.is_file():
        raise MinerURunError(
            f"MinerU produced no result bundle for {pdf.name}; expected {result_zip}."
        )
    try:
        with zipfile.ZipFile(result_zip) as bundle:
            # ZipFile.extractall drops absolute and ".." member paths, so every
            # member lands inside ``target``.
            bundle.extractall(target)
    except zipfile.BadZipFile as exc:
        raise MinerURunError(f"MinerU result bundle for {pdf.name} is not a zip: {exc}") from exc
    if not (target / _ZIP_MIDDLE_JSON).is_file():
        raise MinerURunError(
            f"MinerU result bundle for {pdf.name} has no {_ZIP_MIDDLE_JSON}; "
            "is the installed MinerU older than 4.0?"
        )
    return target


def _stage_artifacts(
    bundle: Path, output_dir: Path, *, stem: str, command: list[str]
) -> MinerURunResult:
    """Copy the bundle's artifacts flat under ``output_dir`` with a stable ``stem`` prefix.

    Downstream stages expect artifacts directly under ``sources/parsed/<doc_id>/``
    named for the workspace doc id. Markdown and middle JSON reference images as
    ``images/<file>``, so ``images/`` keeps its name next to them.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def stage_file(member: str, suffix: str) -> Path | None:
        candidate = bundle / member
        if not candidate.is_file():
            return None
        target = output_dir / f"{stem}{suffix}"
        shutil.copy2(candidate, target)
        written.append(target)
        return target

    try:
        staged_middle = stage_file(_ZIP_MIDDLE_JSON, MINERU_MIDDLE_JSON_SUFFIX)
        assert staged_middle is not None  # checked by _extract_result_bundle
        markdown = stage_file(_ZIP_MARKDOWN, ".md")
        structured_content = stage_file(_ZIP_STRUCTURED_CONTENT, "_structured_content.json")
        model_output = stage_file(_ZIP_MODEL_OUTPUT, "_model_output.json")

        images_dir: Path | None = None
        source_images = bundle / _ZIP_IMAGES_DIR
        if source_images.is_dir():
            images_dir = output_dir / "images"
            if images_dir.exists():
                shutil.rmtree(images_dir)
            shutil.copytree(source_images, images_dir)
            written.append(images_dir)
    except OSError:
        # Don't leave a half-staged parsed dir behind on a copy failure.
        for path in written:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        raise

    return MinerURunResult(
        middle_json=staged_middle,
        command=command,
        markdown=markdown,
        structured_content=structured_content,
        model_output=model_output,
        images_dir=images_dir,
    )
