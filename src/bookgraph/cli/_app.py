from __future__ import annotations

import typer

app = typer.Typer(help="BookGraph: pluggable document-to-graph-wiki pipeline.")
wiki_app = typer.Typer(help="Wiki backend command interfaces.")
reading_plan_app = typer.Typer(help="Reading-plan command interfaces.")
index_app = typer.Typer(help="Search/graph index command interfaces.")
app.add_typer(wiki_app, name="wiki")
app.add_typer(reading_plan_app, name="reading-plan")
app.add_typer(index_app, name="index")
