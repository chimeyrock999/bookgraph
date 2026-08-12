from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.utils import MINERU_MIDDLE_JSON_SUFFIX

CommandRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

_WORK_SUBDIR = "_mineru"


class MinerUNotInstalledError(RuntimeError):
    """Raised when the MinerU executable is not on PATH."""


class MinerURunError(RuntimeError):
    """Raised when MinerU runs but does not produce usable output."""


@dataclass(frozen=True)
class MinerURunResult:
    """Paths to the MinerU artifacts staged under the document's parsed dir."""

    middle_json: Path
    command: list[str]
    markdown: Path | None = None
    layout_pdf: Path | None = None
    span_pdf: Path | None = None
    content_list: Path | None = None
    images_dir: Path | None = None

    def artifacts(self) -> dict[str, Path]:
        """Return staged artifacts keyed by role, skipping the ones MinerU omitted."""

        mapping = {
            "middle_json": self.middle_json,
            "markdown": self.markdown,
            "layout_pdf": self.layout_pdf,
            "span_pdf": self.span_pdf,
            "content_list": self.content_list,
            "images_dir": self.images_dir,
        }
        return {role: path for role, path in mapping.items() if path is not None}


@dataclass
class MinerURunner:
    """Invoke MinerU on a raw PDF to produce ``*_middle.json`` and side artifacts.

    MinerU is a heavy external tool, so it stays out of the base install and is
    invoked as a subprocess rather than imported. ``run_process`` can be injected
    to exercise the runner without MinerU installed; when it is ``None`` the
    default subprocess runner is used and the executable is required on PATH.

    The runner owns only the "invoke the heavy process" step. Turning its
    ``*_middle.json`` into a canonical ``document.json`` remains the job of
    :class:`bookgraph.parsers.mineru.MinerUMiddleJsonParser` via ``bookgraph parse``.
    """

    name: str = "mineru"
    command: str = "mineru"
    method: str = "auto"
    backend: str | None = None
    run_process: CommandRunner | None = field(default=None)

    def run(self, pdf: Path, output_dir: Path) -> MinerURunResult:
        if pdf.suffix.lower() != ".pdf":
            raise UnsupportedSourceError(
                f"{pdf.name}: {self.name} runner only accepts raw .pdf input."
            )
        if not pdf.is_file():
            raise UnsupportedSourceError(f"PDF not found: {pdf}")

        runner = self.run_process
        if runner is None:
            if shutil.which(self.command) is None:
                raise MinerUNotInstalledError(
                    f"MinerU executable '{self.command}' not found on PATH. "
                    "Install with: uv sync --extra mineru"
                )
            runner = _default_run_process

        work_dir = output_dir / _WORK_SUBDIR
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        argv = self._build_argv(pdf, work_dir)
        completed = runner(argv)
        if completed.returncode != 0:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise MinerURunError(
                f"MinerU failed on {pdf.name} (exit {completed.returncode}): "
                f"{(completed.stderr or '').strip() or 'no stderr'}"
            )

        middle_json = _locate_middle_json(work_dir)
        if middle_json is None:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise MinerURunError(
                f"MinerU produced no *{MINERU_MIDDLE_JSON_SUFFIX} for {pdf.name}. "
                f"Searched under {work_dir}."
            )

        result = _stage_artifacts(middle_json, output_dir, stem=output_dir.name, command=argv)
        shutil.rmtree(work_dir, ignore_errors=True)
        return result

    def _build_argv(self, pdf: Path, work_dir: Path) -> list[str]:
        argv = [self.command, "-p", str(pdf), "-o", str(work_dir), "-m", self.method]
        if self.backend:
            argv += ["-b", self.backend]
        return argv


def _default_run_process(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603


def _locate_middle_json(work_dir: Path) -> Path | None:
    """Find MinerU's middle JSON without hardcoding its nested layout.

    MinerU's output directory structure varies across versions (``<name>/auto/``
    and similar), so the file is located by suffix instead of a fixed path.
    """

    matches = sorted(work_dir.rglob(f"*{MINERU_MIDDLE_JSON_SUFFIX}"))
    return matches[0] if matches else None


def _stage_artifacts(
    middle_json: Path, output_dir: Path, *, stem: str, command: list[str]
) -> MinerURunResult:
    """Copy MinerU artifacts flat under ``output_dir`` with a stable ``stem`` prefix.

    MinerU names its outputs after the source stem and nests them; downstream
    stages expect artifacts directly under ``sources/parsed/<doc_id>/`` named for
    the workspace doc id, so each artifact is copied up under ``<stem>...``.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    source_dir = middle_json.parent
    mineru_stem = middle_json.name[: -len(MINERU_MIDDLE_JSON_SUFFIX)]

    staged_middle = output_dir / f"{stem}{MINERU_MIDDLE_JSON_SUFFIX}"
    shutil.copy2(middle_json, staged_middle)

    def stage_file(suffix: str) -> Path | None:
        candidate = source_dir / f"{mineru_stem}{suffix}"
        if not candidate.is_file():
            return None
        target = output_dir / f"{stem}{suffix}"
        shutil.copy2(candidate, target)
        return target

    images_dir: Path | None = None
    source_images = source_dir / "images"
    if source_images.is_dir():
        images_dir = output_dir / "images"
        if images_dir.exists():
            shutil.rmtree(images_dir)
        shutil.copytree(source_images, images_dir)

    return MinerURunResult(
        middle_json=staged_middle,
        command=command,
        markdown=stage_file(".md"),
        layout_pdf=stage_file("_layout.pdf"),
        span_pdf=stage_file("_span.pdf"),
        content_list=stage_file("_content_list.json"),
        images_dir=images_dir,
    )
