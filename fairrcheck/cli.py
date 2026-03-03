"""
cli.py — Typer-based CLI for fairrcheck.

Commands
--------
  fairrcheck scan <path> [--out DIR] [--mode development|publication] [--llm]
  fairrcheck advise <path> [--out DIR]
  fairrcheck fix <path> [--out DIR] [--apply]
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from .llm import LLMConfig
from .registry import load_registry
from .reporters.html_reporter import write_html
from .reporters.json_reporter import write_json
from .reporters.pdf_reporter import write_pdf
from .scanner import collect_excerpts, run_scan

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="fairrcheck",
    help="FAIRR compliance checker for HPC / computational research projects.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()

_LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}


def _setup_logging(level: str = "warning") -> None:
    logging.basicConfig(
        level=_LOG_LEVELS.get(level, logging.WARNING),
        format="%(levelname)s | %(name)s | %(message)s",
    )


# ---------------------------------------------------------------------------
# Shared options
# ---------------------------------------------------------------------------

PathArg = typer.Argument(..., help="Project directory to analyse.")

OutOption = typer.Option(
    None, "--out", "-o",
    help="Output directory for reports (default: <project_path>/fairrcheck_out).",
)

ModeOption = typer.Option(
    "development", "--mode", "-m",
    help="Evaluation mode: development | publication.",
)

RegistryOption = typer.Option(
    None, "--registry",
    help="Path to FAIRR registry YAML (default: bundled config).",
)

VerboseOption = typer.Option(
    False, "--verbose", "-v",
    help="Enable verbose logging.",
)


def _resolve_out(project_path: Path, out: Optional[Path]) -> Path:
    if out:
        return Path(out).resolve()
    return project_path / "fairrcheck_out"


def _grade_colour(grade: str) -> str:
    return {"A": "green", "B": "green", "C": "yellow", "D": "red", "F": "red"}.get(
        grade, "white"
    )


def _print_summary(result: dict) -> None:
    """Print a colourful summary table to the terminal."""
    overall = result["overall_fairr_score"]
    grade = result["grade"]
    colour = _grade_colour(grade)

    llm_line = ""
    if result.get("llm_used"):
        model = result.get("llm_model") or "unknown model"
        llm_line = f"\n[dim]LLM model:[/dim] {model}"

    console.print()
    console.print(
        Panel(
            f"[bold {colour}]{overall * 100:.1f}%[/bold {colour}]  Grade [bold]{grade}[/bold]\n"
            f"[dim]Project:[/dim] {result['project_name']}\n"
            f"[dim]Mode:[/dim] {result['scan_mode']}   "
            f"[dim]LLM:[/dim] {'yes' if result['llm_used'] else 'no'}"
            f"{llm_line}",
            title="[bold blue]Overall FAIRR Score[/bold blue]",
            border_style="blue",
            padding=(0, 2),
        )
    )

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Principle", width=10)
    table.add_column("Name", width=14)
    table.add_column("Score", justify="right", width=8)
    table.add_column("Weight", justify="right", width=8)
    table.add_column("Impl.", justify="right", width=8)

    p_names = {
        "F": "Findable", "A": "Accessible",
        "I": "Interoperable", "R": "Reusable", "R2": "Reproducible",
    }

    for p, data in result["principles"].items():
        norm = data["normalised_score"] * 100
        weight = data["weight"] * 100
        impl = data["implemented_count"]
        total = data["total_metrics"]
        c = "green" if norm >= 70 else ("yellow" if norm >= 40 else "red")
        table.add_row(
            p,
            p_names.get(p, p),
            f"[{c}]{norm:.0f}%[/{c}]",
            f"{weight:.0f}%",
            f"{impl}/{total}",
        )

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@app.command()
def scan(
    path: Path = PathArg,
    out: Optional[Path] = OutOption,
    mode: str = ModeOption,
    llm: bool = typer.Option(False, "--llm", help="Use LLM to reduce false negatives."),
    registry: Optional[Path] = RegistryOption,
    verbose: bool = VerboseOption,
    no_pdf: bool = typer.Option(False, "--no-pdf", help="Skip PDF generation."),
) -> None:
    """
    Scan a project directory and evaluate FAIRR metrics.

    Deterministic for all implemented metrics; optionally augment with LLM.
    Always includes all metrics in the output (unimplemented marked null).
    """
    _setup_logging("debug" if verbose else "warning")

    project_path = path.resolve()
    if not project_path.exists():
        console.print(f"[red]Error:[/red] Path does not exist: {project_path}")
        raise typer.Exit(1)

    if mode not in ("development", "publication"):
        console.print("[red]Error:[/red] --mode must be 'development' or 'publication'.")
        raise typer.Exit(1)

    llm_config: Optional[LLMConfig] = None
    if llm:
        llm_config = LLMConfig()
        if not llm_config.is_configured:
            console.print(
                "[yellow]Warning:[/yellow] --llm requested but LLM not configured "
                "(FAIRRCHECK_LLM_BASE_URL / FAIRRCHECK_LLM_MODEL not set). "
                "Running deterministic scan only."
            )
            llm = False
            llm_config = None
        else:
            console.print(
                f"[blue]LLM:[/blue] {llm_config.model}  "
                f"[dim]({llm_config.base_url})[/dim]"
            )

    out_dir = _resolve_out(project_path, out)
    # LLM runs get distinct file names so they never overwrite deterministic ones.
    file_suffix = "_llm" if llm else ""

    # --- build progress display ---
    # Two tasks: overall metric sweep + LLM call indicator (only shown when --llm)
    p_names = {
        "F": "Findable", "A": "Accessible",
        "I": "Interoperable", "R": "Reusable", "R2": "Reproducible",
    }

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )

    with progress:
        sweep_task = progress.add_task("Scanning metrics…", total=None)  # total set later
        llm_task   = progress.add_task("", total=None, visible=False)

        def on_metric_start(metric_id: str, principle: str, idx: int, total: int) -> None:
            p_full = p_names.get(principle, principle)
            progress.update(
                sweep_task,
                total=total,
                completed=idx,
                description=f"[bold]{p_full}[/bold] — {metric_id}",
            )

        def on_llm_start(metric_id: str, principle: str) -> None:
            p_full = p_names.get(principle, principle)
            progress.update(
                llm_task,
                visible=True,
                description=(
                    f"  [cyan]↳ LLM[/cyan] {p_full} / {metric_id}  "
                    f"[dim]{llm_config.model if llm_config else ''}[/dim]"
                ),
            )

        result = run_scan(
            project_path=project_path,
            mode=mode,
            use_llm=llm,
            llm_config=llm_config,
            registry_path=registry,
            on_metric_start=on_metric_start,
            on_llm_start=on_llm_start if llm else None,
        )

        # Finish progress
        progress.update(sweep_task, completed=result["max_score"], description="Writing reports…")
        progress.update(llm_task, visible=False)

        json_path = write_json(result, out_dir, filename=f"report{file_suffix}.json")
        html_path = write_html(result, out_dir, filename=f"report{file_suffix}.html")
        pdf_path: Optional[Path] = None
        if not no_pdf:
            try:
                pdf_path = write_pdf(result, out_dir, filename=f"report{file_suffix}.pdf")
            except ImportError:
                console.print("[yellow]Skipping PDF:[/yellow] reportlab not installed.")

    _print_summary(result)

    console.print("[green]Reports written:[/green]")
    console.print(f"  JSON : {json_path}")
    console.print(f"  HTML : {html_path}")
    if pdf_path:
        console.print(f"  PDF  : {pdf_path}")
    console.print()


# ---------------------------------------------------------------------------
# advise
# ---------------------------------------------------------------------------


@app.command()
def advise(
    path: Path = PathArg,
    out: Optional[Path] = OutOption,
    llm_scan: bool = typer.Option(
        False, "--llm-scan",
        help="Base advice on an LLM-augmented scan (report_llm.json) instead of the default deterministic scan (report.json).",
    ),
    registry: Optional[Path] = RegistryOption,
    verbose: bool = VerboseOption,
) -> None:
    """
    Provide LLM-powered improvement advice based on current FAIRR scores.

    By default uses the deterministic scan report (report.json).
    Pass --llm-scan to use the LLM-augmented scan report (report_llm.json).
    If the required report does not exist it is generated automatically.

    Requires FAIRRCHECK_LLM_BASE_URL and FAIRRCHECK_LLM_MODEL to be set.
    """
    from .llm import llm_advise

    _setup_logging("debug" if verbose else "warning")

    llm_config = LLMConfig()
    try:
        llm_config.require()
    except RuntimeError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    project_path = path.resolve()
    out_dir = _resolve_out(project_path, out)
    reg = load_registry(registry)

    report_filename = "report_llm.json" if llm_scan else "report.json"
    report_path = out_dir / report_filename
    scan_label = "LLM-augmented" if llm_scan else "deterministic"

    if report_path.exists():
        console.print(
            f"[blue]Using cached {scan_label} scan report:[/blue] [dim]{report_path}[/dim]"
        )
        scan_result = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        console.print(
            f"[blue]No cached {scan_label} scan found — running scan now…[/blue]"
        )
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            console=console, transient=True,
        ) as progress:
            progress.add_task("Scanning…", total=None)
            scan_result = run_scan(
                project_path=project_path,
                mode="development",
                use_llm=llm_scan,
                llm_config=llm_config if llm_scan else None,
                registry=reg,
            )
        write_json(scan_result, out_dir, filename=report_filename)
        console.print(f"[green]Scan report saved:[/green] [dim]{report_path}[/dim]")

    _print_summary(scan_result)

    # Compute how many low-scoring implemented metrics will be sent to the LLM
    max_s = scan_result.get("max_score", 2)
    low_metric_ids = [
        r["metric_id"] for r in scan_result.get("metrics", [])
        if r.get("score") is not None and r["score"] < max_s
    ]
    console.print(
        f"[blue]Consulting LLM for improvement advice…[/blue] "
        f"[dim]({len(low_metric_ids)} low-scoring metrics: {', '.join(low_metric_ids) or 'none'})[/dim]"
    )
    excerpts = collect_excerpts(project_path)

    try:
        advice = llm_advise(llm_config, scan_result, project_path, excerpts, registry=reg)
    except Exception as exc:
        console.print(f"[red]LLM advise failed:[/red] {exc}")
        raise typer.Exit(1)

    if advice.get("error"):
        console.print(f"[red]LLM returned an unparseable response:[/red] {advice['error']}")
        console.print("[dim]Tip: run with --verbose to see the raw LLM output.[/dim]")
        raise typer.Exit(1)

    suggestions = advice.get("suggestions", [])

    if not suggestions:
        raw_hint = advice.get("_raw", "")
        console.print("[yellow]No suggestions returned by LLM.[/yellow]")
        if raw_hint:
            console.print(f"[dim]Raw LLM response:[/dim]\n{raw_hint[:800]}")
        else:
            console.print("[dim]Tip: run with --verbose to see what was sent to and returned by the LLM.[/dim]")
        raise typer.Exit(0)

    # Display suggestions
    table = Table(title="LLM Improvement Suggestions", show_header=True, header_style="bold blue")
    table.add_column("Priority", width=8, justify="center")
    table.add_column("Metric", width=12)
    table.add_column("Advice", min_width=40)

    for s in sorted(suggestions, key=lambda x: x.get("priority", 9)):
        pri = str(s.get("priority", "?"))
        mid = s.get("metric_id", "")
        msg = s.get("message", "")
        snippet = s.get("example_snippet", "")
        full = msg + (f"\n[dim]Example: {snippet}[/dim]" if snippet else "")
        table.add_row(pri, mid, full)

    console.print(table)

    # Write advice JSON
    advice_path = out_dir / "advice.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    advice_path.write_text(
        json.dumps({"scan_result": scan_result, "advice": advice}, indent=2),
        encoding="utf-8",
    )
    console.print(f"\n[green]Advice saved:[/green] {advice_path}")


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------


@app.command()
def fix(
    path: Path = PathArg,
    out: Optional[Path] = OutOption,
    apply: bool = typer.Option(False, "--apply", help="Apply patches after confirmation."),
    use_aider: bool = typer.Option(
        False, "--aider",
        help="Use Aider subprocess to generate patches instead of the default LLM patch.",
    ),
    llm_scan: bool = typer.Option(
        False, "--llm-scan",
        help="Base fixes on an LLM-augmented scan (report_llm.json) instead of the default deterministic scan (report.json).",
    ),
    registry: Optional[Path] = RegistryOption,
    verbose: bool = VerboseOption,
) -> None:
    """
    Generate fix patches for FAIRR improvements using the LLM.

    By default patches are generated directly via the LLM.
    Pass --aider to use the Aider subprocess instead (requires Aider installed
    and FAIRRCHECK_LLM_MODEL to be set).

    By default uses the deterministic scan report (report.json).
    Pass --llm-scan to use the LLM-augmented scan report (report_llm.json).
    If the required report does not exist it is generated automatically.

    Requires FAIRRCHECK_LLM_BASE_URL and FAIRRCHECK_LLM_MODEL.
    Only modifies: README.md, CITATION.cff, metadata.json.
    """
    from .agent import FixAgent
    from .llm import llm_advise

    _setup_logging("debug" if verbose else "warning")

    llm_config = LLMConfig()
    try:
        llm_config.require()
    except RuntimeError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    project_path = path.resolve()
    out_dir = _resolve_out(project_path, out)
    reg = load_registry(registry)

    # Step 1: Scan (reuse cached report if available)
    report_filename = "report_llm.json" if llm_scan else "report.json"
    report_path = out_dir / report_filename
    scan_label = "LLM-augmented" if llm_scan else "deterministic"

    if report_path.exists():
        console.print(
            f"[blue]Step 1/3: Using cached {scan_label} scan report:[/blue] [dim]{report_path}[/dim]"
        )
        scan_result = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        console.print(
            f"[blue]Step 1/3: No cached {scan_label} scan — running scan now…[/blue]"
        )
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            console=console, transient=True,
        ) as progress:
            progress.add_task("Scanning…", total=None)
            scan_result = run_scan(
                project_path=project_path, mode="development",
                use_llm=llm_scan, llm_config=llm_config if llm_scan else None,
                registry=reg,
            )
        write_json(scan_result, out_dir, filename=report_filename)
        console.print(f"[green]Scan report saved:[/green] [dim]{report_path}[/dim]")

    # Step 2: Get advice (reuse cached advice.json if available)
    advice_path = out_dir / "advice.json"
    if advice_path.exists():
        console.print(
            f"[blue]Step 2/3: Using cached advice:[/blue] [dim]{advice_path}[/dim]"
        )
        advice = json.loads(advice_path.read_text(encoding="utf-8")).get("advice", {})
    else:
        console.print("[blue]Step 2/3: Getting improvement suggestions from LLM…[/blue]")
        excerpts = collect_excerpts(project_path)
        try:
            advice = llm_advise(llm_config, scan_result, project_path, excerpts, registry=reg)
        except Exception as exc:
            console.print(f"[red]LLM advise failed:[/red] {exc}")
            raise typer.Exit(1)

        if advice.get("error"):
            console.print(f"[red]LLM returned an unparseable response:[/red] {advice['error']}")
            console.print("[dim]Tip: run with --verbose to see the raw LLM output.[/dim]")
            raise typer.Exit(1)

        out_dir.mkdir(parents=True, exist_ok=True)
        advice_path.write_text(
            json.dumps({"scan_result": scan_result, "advice": advice}, indent=2),
            encoding="utf-8",
        )
        console.print(f"[green]Advice saved:[/green] [dim]{advice_path}[/dim]")

    suggestions = advice.get("suggestions", [])[:5]  # limit to top 5 for fix
    if not suggestions:
        console.print("[yellow]No improvements suggested. Nothing to fix.[/yellow]")
        raise typer.Exit(0)

    # Step 3: Generate patches
    console.print("[blue]Step 3/3: Generating patches…[/blue]")
    agent = FixAgent(project_path=project_path, llm_config=llm_config, use_aider=use_aider)
    patches = agent.generate(suggestions)

    if not patches:
        console.print("[yellow]Could not generate any patches.[/yellow]")
        raise typer.Exit(0)

    # Show diffs
    for patch in patches:
        mid = patch.get("metric_id", "?")
        diff = patch.get("diff", "")
        problems = patch.get("problems", [])
        method = patch.get("method", "none")

        colour = "red" if problems else "green"
        status = f"[{colour}]{'⚠ PROBLEMS' if problems else '✓ OK'}[/{colour}]"
        console.print(
            Panel(
                diff[:2000] if diff else "[dim](no diff generated)[/dim]",
                title=f"[bold]{mid}[/bold] via {method} {status}",
                border_style="yellow" if problems else "green",
            )
        )
        if problems:
            for p in problems:
                console.print(f"  [red]Problem:[/red] {p}")

    # Save patches
    out_dir.mkdir(parents=True, exist_ok=True)
    patches_path = out_dir / "patches.json"
    patches_path.write_text(json.dumps(patches, indent=2), encoding="utf-8")
    console.print(f"\n[green]Patches saved:[/green] {patches_path}")

    # Apply?
    if apply:
        safe_patches = [p for p in patches if not p.get("problems") and p.get("diff")]
        if not safe_patches:
            console.print("[yellow]No safe patches to apply.[/yellow]")
            raise typer.Exit(0)

        console.print(
            f"\n[yellow]About to apply {len(safe_patches)} patch(es) to:[/yellow] {project_path}"
        )
        confirm = typer.confirm("Apply patches? (y/N)", default=False)
        if confirm:
            for patch in safe_patches:
                ok = agent.apply(patch)
                status_str = "[green]✓ Applied[/green]" if ok else "[red]✗ Failed[/red]"
                console.print(f"  {status_str} {patch['metric_id']}")
        else:
            console.print("[dim]Dry-run complete — no changes made.[/dim]")
    else:
        console.print("\n[dim]Dry-run mode. Use --apply to apply patches.[/dim]")


# ---------------------------------------------------------------------------
# info sub-command — show registry summary
# ---------------------------------------------------------------------------


@app.command()
def info(
    registry: Optional[Path] = RegistryOption,
) -> None:
    """Show the loaded FAIRR registry summary."""
    reg = load_registry(registry)

    table = Table(title=f"[bold]{reg.name}[/bold]  (v{reg.schema_version})", show_header=True)
    table.add_column("ID", style="cyan", width=14)
    table.add_column("Principle", width=10)
    table.add_column("Name", width=34)
    table.add_column("Impl.", justify="center", width=6)

    for m in reg.metrics:
        impl = "[green]✓[/green]" if m.implemented_in_prototype else "[dim]—[/dim]"
        table.add_row(m.id, m.principle, m.name, impl)

    console.print(table)
    console.print(
        f"\n[dim]Principles:[/dim] {', '.join(reg.principles)} | "
        f"[dim]Scale:[/dim] {reg.scale} | "
        f"[dim]Weights:[/dim] {json.dumps(reg.weights)}"
    )
    console.print(
        f"[dim]Implemented:[/dim] {len(reg.implemented_metrics)}/{len(reg.metrics)} metrics\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
