from __future__ import annotations

import typer

app = typer.Typer(help="BookGraph: pluggable document-to-graph-wiki pipeline.")
wiki_app = typer.Typer(help="Wiki backend command interfaces.")
reading_plan_app = typer.Typer(help="Reading-plan command interfaces.")
index_app = typer.Typer(help="Search/graph index command interfaces.")
concepts_app = typer.Typer(help="Concept registry: aliases, merge suggestions, lint, review.")
llmwiki_app = typer.Typer(help="Optional llmwiki MCP integration (compiled-wiki queries).")
export_app = typer.Typer(help="Reader-facing exports (translated reading editions).")
app.add_typer(wiki_app, name="wiki")
app.add_typer(reading_plan_app, name="reading-plan")
app.add_typer(index_app, name="index")
app.add_typer(concepts_app, name="concepts")
app.add_typer(llmwiki_app, name="llmwiki")
app.add_typer(export_app, name="export")
