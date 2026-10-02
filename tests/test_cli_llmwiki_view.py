"""CLI tests for ``bookgraph llmwiki view`` and the llmwiki 1.4 compile options."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.cli import llmwiki as llmwiki_cli
from test_cli_llmwiki import _write_sections_manifest

runner = CliRunner()


def _compiled_workspace(workspace: Path) -> Path:
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    state = workspace / "llmwiki" / ".llmwiki" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}\n")
    return (workspace / "llmwiki").resolve()


def _fake_llmwiki(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], Path | None]]:
    calls: list[tuple[list[str], Path | None]] = []

    def fake_call(command: list[str], cwd: Path | None = None) -> int:
        calls.append((command, cwd))
        return 0

    monkeypatch.setattr(llmwiki_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(llmwiki_cli.subprocess, "call", fake_call)
    return calls


def test_llmwiki_view_is_registered() -> None:
    result = runner.invoke(app, ["llmwiki", "--help"])

    assert result.exit_code == 0, result.output
    assert "view" in result.output


def test_llmwiki_view_runs_in_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _compiled_workspace(tmp_path)
    calls = _fake_llmwiki(monkeypatch)

    result = runner.invoke(app, ["llmwiki", "view", str(tmp_path)])

    assert result.exit_code == 0, result.output
    # `llmwiki view` has no --root option; it serves the current directory.
    assert calls == [(["llmwiki", "view"], project)]


def test_llmwiki_view_forwards_open_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _compiled_workspace(tmp_path)
    calls = _fake_llmwiki(monkeypatch)

    result = runner.invoke(app, ["llmwiki", "view", str(tmp_path), "--open", "--port", "8123"])

    assert result.exit_code == 0, result.output
    assert calls == [(["llmwiki", "view", "--port", "8123", "--open"], project)]


def test_llmwiki_view_print_emits_cwd_command(tmp_path: Path) -> None:
    # --print is pure command generation: no compiled project required.
    workspace = tmp_path / "my workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "view", str(workspace), "--print", "--open"])

    assert result.exit_code == 0, result.output
    project = (workspace / "llmwiki").resolve()
    assert result.output.strip() == f"cd '{project}' && llmwiki view --open"


def test_llmwiki_view_uncompiled_project(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "view", str(tmp_path)])

    assert result.exit_code != 0
    assert "No compiled llmwiki project" in result.output


def test_llmwiki_view_missing_workspace(tmp_path: Path) -> None:
    result = runner.invoke(app, ["llmwiki", "view", str(tmp_path / "nope")])

    assert result.exit_code != 0
    assert "Workspace not found" in result.output


def test_llmwiki_view_requires_llmwiki_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _compiled_workspace(tmp_path)
    monkeypatch.setattr(llmwiki_cli.shutil, "which", lambda name: None)

    result = runner.invoke(app, ["llmwiki", "view", str(tmp_path)])

    assert result.exit_code != 0
    assert "llmwiki is not installed" in result.output


def test_llmwiki_bridge_compile_forwards_compile_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")
    instructions = tmp_path / "editorial.md"
    instructions.write_text("Cite with ^[file.md:L-L].\n")
    calls = _fake_llmwiki(monkeypatch)
    # Pass the instructions file relative to the caller's cwd: compile runs inside
    # llmwiki/, so the bridge must hand llmwiki an absolute path.
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "llmwiki", "bridge", str(tmp_path), "deep-work", "--compile",
            "--review", "--lang", "vi", "--instructions", "editorial.md",
            "--concurrency", "3",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            [
                "llmwiki", "compile", "--review", "--lang", "vi",
                "--instructions", str(instructions.resolve()), "--concurrency", "3",
            ],
            (tmp_path / "llmwiki").resolve(),
        )
    ]  # fmt: skip


def test_llmwiki_bridge_compile_print_includes_compile_options(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(
        app,
        ["llmwiki", "bridge", str(tmp_path), "deep-work", "--compile", "--print", "--review",
         "--lang", "Vietnamese"],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    project = (tmp_path / "llmwiki").resolve()
    assert result.output.strip().splitlines()[-1] == (
        f"cd {project} && llmwiki compile --review --lang Vietnamese"
    )


@pytest.mark.parametrize(
    "option",
    [["--review"], ["--lang", "vi"], ["--instructions", "x.md"], ["--concurrency", "2"]],
)
def test_llmwiki_bridge_compile_options_require_compile(tmp_path: Path, option: list[str]) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work", *option])

    assert result.exit_code != 0
    assert f"{option[0]} applies to the compile step" in result.output
    assert not (tmp_path / "llmwiki").exists()


def test_llmwiki_bridge_compile_rejects_missing_instructions(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(
        app,
        ["llmwiki", "bridge", str(tmp_path), "deep-work", "--compile", "--print",
         "--instructions", str(tmp_path / "missing.md")],
    )  # fmt: skip

    assert result.exit_code != 0
    assert "Instructions file not found" in result.output
    assert not (tmp_path / "llmwiki").exists()


def test_llmwiki_bridge_compile_rejects_zero_concurrency(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(
        app,
        ["llmwiki", "bridge", str(tmp_path), "deep-work", "--compile", "--print",
         "--concurrency", "0"],
    )  # fmt: skip

    assert result.exit_code != 0


def test_llmwiki_bridge_new_project_writes_recursive_config(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "layout: sources/<doc_id>/" in result.output
    config = tmp_path / "llmwiki" / ".llmwiki" / "config.json"
    assert json.loads(config.read_text()) == {"version": 1, "sources": {"recursive": True}}
    sources = tmp_path / "llmwiki" / "sources" / "deep-work"
    assert sorted(path.name for path in sources.iterdir()) == [
        "deep-work.chapter-1.md",
        "deep-work.intro.md",
    ]


def test_llmwiki_bridge_existing_flat_project_stays_flat(tmp_path: Path) -> None:
    # A project bridged before the nested layout keeps staging flat, so a re-run
    # does not leave a second copy of every section for llmwiki to compile.
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")
    flat = tmp_path / "llmwiki" / "sources"
    flat.mkdir(parents=True)
    (flat / "deep-work.intro.md").write_text("old\n")

    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "layout: sources/ (flat)" in result.output
    assert (flat / "deep-work.chapter-1.md").is_file()
    assert not (flat / "deep-work").exists()
    assert not (tmp_path / "llmwiki" / ".llmwiki" / "config.json").exists()
