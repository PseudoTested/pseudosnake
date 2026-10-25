"""Main entry point for the PseudoSnake CLI.

Pipeline overview
-----------------
1. **Baseline validation** — run the test suite N times on the unmodified
   project.  If it ever fails, the project is unstable; abort.

2. **Dynamic coverage** — instrument source functions with lightweight call
   counters, run the test suite, and record which functions were executed.

3. **Mutation loop** — for every discovered function, generate simple return
   mutants based on the function's annotated return type.  Apply each mutant,
   run the full test suite N times, restore the file, and classify the result:

   * ``KILLED``   — tests failed → behaviour is verified.
   * ``SURVIVED`` — tests still pass → *pseudo-tested* (called but not verified).
   * ``NO_TESTS`` — no tests collected → function is untested.

4. **Report** — write a JSON report with every function, every mutant result,
   and metadata about the run.
"""

import ast
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from pseudosnake.backup import (
    backup_file,
    cleanup_backup,
    cleanup_snapshot,
    create_snapshot,
    get_snapshot_dir,
    get_snapshot_storage_info,
    list_snapshots,
    restore_file,
    restore_snapshot,
)
from pseudosnake.helpers import (
    build_metadata,
    build_uncovered_entry,
    digest,
)
from pseudosnake.discover import (
    FunctionInfo,
    find_functions,
    find_python_files,
    find_test_files,
)
from pseudosnake.dynamic_coverage import (
    CoverageMap,
    FunctionCoverage,
    build_coverage_bootstrap,
    collect_function_coverage,
    function_key,
    instrument_file_source,
    instrument_test_file,
    is_already_instrumented,
    load_dynamic_coverage_results,
)
from pseudosnake.mutate import (
    MutationApplicationError,
    apply_mutant,
    generate_mutants,
)
from pseudosnake.report import (
    build_file_entry,
    build_function_entry,
    build_mutant_entry,
    build_report,
    output_report,
)
from pseudosnake.source_edit import SourceEditError, locate_function_nodes
from pseudosnake.runner import (
    RunResult,
    classify_result,
    run_tests_repeated,
    run_tests_with_env,
)

# cli application
app = typer.Typer(
    name="pseudosnake",
    help=(
        "PseudoSnake identifies pseudo-tested statements and methods in Python "
        "packages."
    ),
    no_args_is_help=False,
)

# rich console for coloured terminal output
console = Console()

# phase 0 — baseline stability check


def _validate_baseline(
    test_command: str,
    project_dir: Path,
    num_test_runs: int,
    timeout: int | None = 300,
) -> None:
    """Run the test suite N times on the unmodified project.

    Raises :class:`typer.Exit` if any run fails — the project's test suite
    must be deterministic before mutation testing can produce meaningful
    results.
    """
    # run the test command n times on the unmodified source code
    baseline = run_tests_repeated(
        test_command, project_dir, num_test_runs, timeout=timeout
    )
    # all runs passed — baseline is stable
    if baseline.overall.exit_code == 0:
        return

    # at least one run failed — report the instability
    console.print("[bold red]ERROR: Baseline test suite unstable.[/bold red]")
    exit_codes = [run.exit_code for run in baseline.per_run]
    console.print(
        f"Observed baseline exit codes across {num_test_runs} run(s): {exit_codes}"
    )
    # print the stderr from the first failing run for debugging
    for run in baseline.per_run:
        if run.exit_code != 0:
            console.print("[bold]Output from first failing run:[/bold]")
            console.print(run.stderr or run.stdout or "(no output)")
            break

    # exit code 5 means pytest found no tests — likely a wrong project-dir
    if baseline.overall.exit_code == 5:
        console.print(
            "Use the repository root for [cyan]--project-dir[/cyan], "
            "not a package subfolder."
        )

    # abort with exit code 2
    raise typer.Exit(code=2)


# phase 1 — dynamic coverage


def _run_dynamic_coverage_phase(
    files: list[Path],
    project_dir: Path,
    test_command: str,
    test_files: list[Path] | None = None,
) -> CoverageMap:
    """Instrument files, then return detailed coverage grouped by source file.

    The records retain total and per-test call counts. The mutation pipeline
    uses the same records to skip functions that were never called. Every
    modified file is restored and verified before this function returns.
    """
    # keep track of backups so we can restore everything in the finally block
    backups: dict[Path, Path] = {}
    original_functions: dict[Path, list[FunctionInfo]] = {}
    coverage_path = (
        Path(tempfile.gettempdir())
        / f"pseudosnake_dynamic_coverage_{uuid.uuid4().hex}.json"
    )
    # Reuse discovery already performed by ``analyze`` when available while
    # preserving this helper's convenient standalone behavior in tests.
    discovered_test_files = (
        find_test_files(project_dir) if test_files is None else test_files
    )

    try:
        # Test markers set the shared active-test context used by source
        # counters. Only actual test functions are marked; fixtures and helper
        # functions must not replace their calling test's identity.
        instrumented_tests = 0
        for test_path in discovered_test_files:
            source = test_path.read_text(encoding="utf-8")
            if is_already_instrumented(source):
                console.print(
                    f"  [yellow]Warning: {test_path.name} already instrumented"
                    f" from a prior run — skipping[/yellow]"
                )
                continue
            test_functions = [
                function
                for function in find_functions(test_path)
                if function.name.startswith("test")
            ]
            if not test_functions:
                continue
            backups[test_path] = backup_file(test_path)
            relative = str(test_path.relative_to(project_dir))
            instrumented = instrument_test_file(source, test_functions, relative)
            try:
                compile(instrumented, str(test_path), "exec")
            except SyntaxError as exc:
                console.print(
                    f"  [bold red]Instrumentation produced invalid Python for"
                    f" {relative}: {exc}[/bold red]"
                )
                raise typer.Exit(code=2) from exc
            test_path.write_text(instrumented, encoding="utf-8")
            instrumented_tests += len(test_functions)
            console.print(f"  Instrumented {len(test_functions)} test(s) in {relative}")

        # Source counters record both the total hit count and whichever test
        # marker is active. Source files that are also tests were handled above
        # and are intentionally not instrumented a second time.
        instrumented_test_paths = set(backups)
        instrumented_count = 0
        for file_path in files:
            if file_path in instrumented_test_paths:
                continue
            source = file_path.read_text(encoding="utf-8")
            if is_already_instrumented(source):
                console.print(
                    f"  [yellow]Warning: {file_path.name} already instrumented"
                    f" from a prior run — skipping[/yellow]"
                )
                continue
            # find all functions in this source file
            functions = find_functions(file_path)
            if not functions:
                continue
            original_functions[file_path] = functions
            # back up the original source file
            backups[file_path] = backup_file(file_path)
            relative = str(file_path.relative_to(project_dir))
            # inject hit counters into each function body
            instrumented = instrument_file_source(source, functions, relative)
            try:
                compile(instrumented, str(file_path), "exec")
            except SyntaxError as exc:
                console.print(
                    f"  [bold red]Instrumentation produced invalid Python for"
                    f" {relative}: {exc}[/bold red]"
                )
                raise typer.Exit(code=2) from exc
            file_path.write_text(instrumented, encoding="utf-8")
            instrumented_count += len(functions)
            console.print(f"  Instrumented {len(functions)} function(s) in {relative}")

        console.print(
            f"  Total: {instrumented_tests} test(s) + "
            f"{instrumented_count} source function(s) instrumented."
        )

        # run the test suite with instrumented code
        # the env var tells each module where to flush their counter data
        with tempfile.TemporaryDirectory(prefix="pseudosnake_bootstrap_") as temp_dir:
            bootstrap_dir = Path(temp_dir)
            (bootstrap_dir / "sitecustomize.py").write_text(
                build_coverage_bootstrap(), encoding="utf-8"
            )
            python_path = str(bootstrap_dir)
            if existing_path := os.environ.get("PYTHONPATH"):
                python_path = os.pathsep.join((python_path, existing_path))
            coverage_run = run_tests_with_env(
                test_command,
                project_dir,
                extra_env={
                    "PSEUDOSNAKE_DYN_COV_PATH": str(coverage_path),
                    "PYTHONPATH": python_path,
                },
            )
        if coverage_run.exit_code != 0:
            console.print(
                "  [bold red]Instrumented test run failed; coverage results "
                "are invalid.[/bold red]"
            )
            if coverage_run.stdout:
                console.print(coverage_run.stdout, markup=False)
            if coverage_run.stderr:
                console.print(coverage_run.stderr, markup=False)
            raise typer.Exit(code=2)

        # inspect coverage results
        if coverage_path.exists():
            size = coverage_path.stat().st_size
            console.print(
                f"  [cyan]Coverage JSON: {coverage_path} ({size} bytes)[/cyan]"
            )
        else:
            console.print("  [yellow]Warning: Coverage JSON NOT created[/yellow]")

        # load the merged coverage data from all instrumented modules
        coverage_results = load_dynamic_coverage_results(coverage_path)
        if not coverage_results:
            console.print("  [yellow]Warning: No coverage results parsed[/yellow]")
            return {}

        # Preserve the full records for both mutation selection and reporting.
        coverage_map: CoverageMap = {}
        for file_path in files:
            # Use the pre-instrumentation identities. The injected module
            # bootstrap shifts source lines on disk until the finally block
            # restores the original file.
            functions = original_functions.get(file_path, [])
            function_coverage = collect_function_coverage(
                file_path, project_dir, functions, coverage_results
            )
            relative = str(file_path.relative_to(project_dir))
            coverage_map[relative] = function_coverage
            executed_count = sum(
                record.covered for record in function_coverage.values()
            )
            if executed_count:
                console.print(
                    f"  [cyan]{executed_count} executed function(s) "
                    f"in {relative}[/cyan]"
                )
            elif functions:
                console.print(
                    f"  [dim]{relative}: {len(functions)} function(s) "
                    f"instrumented, 0 executed[/dim]"
                )

        return coverage_map

    finally:
        # Mutation testing must never run against instrumented tests or source.
        # Restore and verify every phase-local backup before allowing this
        # function to return; the outer full-run snapshot is a second fallback.
        restore_failures: list[str] = []
        for file_path, backup in backups.items():
            restored = False
            try:
                restore_file(file_path, backup)
                # Compare exact text, not just syntax, so formatting and
                # comments are also guaranteed to be back in place.
                current = file_path.read_text(encoding="utf-8")
                backup_content = backup.read_text(encoding="utf-8")
                if current != backup_content:
                    raise RuntimeError("restored content differs from backup")
                restored = True
            except Exception as exc:
                relative = str(file_path.relative_to(project_dir))
                restore_failures.append(f"{relative}: {exc}")
                console.print(
                    f"  [bold red]ERROR: failed to restore {relative}: {exc}[/bold red]"
                )
            if restored:
                try:
                    cleanup_backup(backup)
                except Exception:
                    pass  # cleanup cannot invalidate an already-verified restore
        try:
            coverage_path.unlink(missing_ok=True)
        except Exception:
            pass
        if restore_failures:
            console.print(
                "  [bold red]Coverage cleanup was incomplete; mutation testing "
                "has been aborted.[/bold red]"
            )
            raise typer.Exit(code=2)


# phase 2 — mutation testing


def _process_file(
    file_path: Path,
    project_dir: Path,
    test_command: str,
    num_test_runs: int,
    progress: Progress,
    function_coverage: dict[str, FunctionCoverage] | None = None,
    timeout: int | None = 300,
) -> dict[str, Any] | None:
    """Analyse all functions in one source file.

    When *function_coverage* is provided (dynamic coverage enabled), only
    functions whose record has calls are mutation-tested.
    Uncovered functions still appear in the report with ``covered: false``.
    """
    # discover all functions in this file via ast parsing
    functions = find_functions(file_path)
    if not functions:
        return None  # no functions to analyse

    # print the file header in the console output
    relative = file_path.relative_to(project_dir)
    console.print(f"\n[bold]{relative}[/bold]")

    function_entries: list[dict[str, Any]] = []
    for f in functions:
        # Direct callers without coverage retain the historical behavior of
        # mutation-testing every function. Normal CLI runs always pass records.
        coverage = (
            None
            if function_coverage is None
            else function_coverage.get(function_key(f), FunctionCoverage())
        )
        covered = coverage is None or coverage.covered

        if covered:
            if function_coverage is not None:
                console.print(f"  [green]Covered[/] [dim]{f.name}[/]")
            # run all mutants for this function
            function_entries.append(
                _process_function(
                    f,
                    test_command,
                    num_test_runs,
                    project_dir,
                    progress,
                    coverage=coverage,
                    timeout=timeout,
                )
            )
        else:
            # function was never executed — skip mutation, mark as uncovered
            console.print(f"  [dim]Not covered[/] [dim]{f.name}[/] - skipping")
            function_entries.append(build_uncovered_entry(f, coverage))

    return build_file_entry(file_path, project_dir, function_entries)


def _process_function(
    func_info: FunctionInfo,
    test_command: str,
    num_test_runs: int,
    project_dir: Path,
    progress: Progress,
    coverage: FunctionCoverage | None = None,
    timeout: int | None = 300,
) -> dict[str, Any]:
    """Run every mutant for one function and return the report entry.

    1. Back up the source file.
    2. For each mutant: apply it, run the test suite N times, restore the file.
    3. Build a function-level entry with all mutant results.
    """
    # generate the list of mutant return statements for this function's type
    mutants = generate_mutants(func_info.return_type)
    # back up the original file so we can restore after each mutation
    backup_path = backup_file(func_info.file_path)
    mutant_entries: list[dict[str, Any]] = []

    try:
        for mutant in mutants:
            # show a spinner in the terminal while this mutant runs
            task = progress.add_task(
                f"[cyan]{func_info.name}[/] → {mutant}", total=None
            )
            # apply mutation → run tests → restore → classify
            test_result, status, verification, run_exit_codes = _run_single_mutant(
                func_info.file_path,
                func_info,
                mutant,
                test_command,
                num_test_runs,
                project_dir,
                backup_path,
                timeout=timeout,
            )
            progress.remove_task(task)

            # build the json entry for this mutant
            mutant_entries.append(
                build_mutant_entry(
                    mutant,
                    test_result,
                    status,
                    verification,
                    run_exit_codes=run_exit_codes,
                    num_test_runs=num_test_runs,
                )
            )
            # print a coloured one-line result to the terminal
            _print_mutant_result(func_info.name, mutant, status)
    finally:
        # always restore the file and clean up the backup, even on error
        restore_file(func_info.file_path, backup_path)
        cleanup_backup(backup_path)

    return build_function_entry(func_info, mutant_entries, coverage=coverage)


def _run_single_mutant(
    file_path: Path,
    func_info: FunctionInfo,
    mutant: str,
    test_command: str,
    num_test_runs: int,
    project_dir: Path,
    backup_path: Path,
    timeout: int | None = 300,
) -> tuple[RunResult, str, dict[str, Any], list[int]]:
    """Apply one mutant, run the test suite, restore, and return the result.

    Verifies that the mutation was actually applied to disk and later restored
    correctly (checked via SHA-256 digests).
    """
    # step 1: apply the mutation
    # read the original source before mutation
    original_source = file_path.read_text(encoding="utf-8")
    source_lines = original_source.splitlines(keepends=True)
    try:
        # replace the exact function body and reject invalid Python before
        # starting an expensive test subprocess.
        mutated_lines = apply_mutant(source_lines, func_info, mutant)
        mutated_source = "".join(mutated_lines)
        compile(mutated_source, str(file_path), "exec")
    except (MutationApplicationError, SyntaxError) as exc:
        restored_source = file_path.read_text(encoding="utf-8")
        test_result = RunResult(
            exit_code=-2,
            stdout="",
            stderr=f"Invalid mutation: {exc}",
            duration=0.0,
        )
        verification = {
            "mutation_applied": False,
            "mutation_restored": restored_source == original_source,
            "original_sha256": digest(original_source),
            "mutated_sha256": digest(original_source),
            "restored_sha256": digest(restored_source),
        }
        return test_result, "INVALID", verification, []
    # write the mutated code back to disk
    file_path.write_text(mutated_source, encoding="utf-8")

    # clear stale .pyc caches so Python recompiles from the mutated source.
    # without this, @dataclass and other decorators can cache old bytecode
    # and the mutant may never actually execute.
    _clear_pyc_cache(file_path)

    # verify the mutation was actually written to disk correctly.
    # also verify that the function body now contains the expected mutant.
    on_disk_mutated = file_path.read_text(encoding="utf-8")
    mutation_applied = (
        on_disk_mutated == mutated_source
        and on_disk_mutated != original_source
        and _verify_mutant_in_file(on_disk_mutated, func_info, mutant)
    )

    if not mutation_applied:
        restore_file(file_path, backup_path)
        restored_source = file_path.read_text(encoding="utf-8")
        test_result = RunResult(
            exit_code=-2,
            stdout="",
            stderr="Invalid mutation: verification failed",
            duration=0.0,
        )
        verification = {
            "mutation_applied": False,
            "mutation_restored": restored_source == original_source,
            "original_sha256": digest(original_source),
            "mutated_sha256": digest(mutated_source),
            "restored_sha256": digest(restored_source),
        }
        return test_result, "INVALID", verification, []

    # step 2: run the test suite
    repeated = run_tests_repeated(
        test_command, project_dir, num_test_runs, timeout=timeout
    )
    test_result = repeated.overall

    # step 3: restore the original file
    restore_file(file_path, backup_path)
    restored_source = file_path.read_text(encoding="utf-8")
    mutation_restored = restored_source == original_source

    # step 4: classify and build verification metadata
    status = classify_result(test_result)
    # sha-256 hashes prove the mutation was correctly applied and restored
    verification = {
        "mutation_applied": mutation_applied,
        "mutation_restored": mutation_restored,
        "original_sha256": digest(original_source),
        "mutated_sha256": digest(mutated_source),
        "restored_sha256": digest(restored_source),
    }
    # collect per-run exit codes for transparency
    run_exit_codes = [run.exit_code for run in repeated.per_run]
    return test_result, status, verification, run_exit_codes


# helpers


def _clear_pyc_cache(file_path: Path) -> None:
    """Delete stale .pyc caches so Python recompiles from the mutated source."""
    cache_dir = file_path.parent / "__pycache__"
    if not cache_dir.is_dir():
        return
    for pyc in cache_dir.iterdir():
        try:
            pyc.unlink(missing_ok=True)
        except OSError:
            pass


def _verify_mutant_in_file(
    mutated_source: str, func_info: FunctionInfo, mutant: str
) -> bool:
    """Check that the target function contains the expected mutant."""
    try:
        # parse the expected statement and locate the exact on-disk target
        # through the same identity rules used when applying the mutation.
        expected = ast.parse(mutant).body
        target = locate_function_nodes(mutated_source, [func_info])[0]
    except (SyntaxError, SourceEditError):
        return False
    if len(expected) != 1:
        return False
    # a valid whole-body mutation contains exactly the requested AST statement
    # and no leftover statements from the original function.
    return len(target.body) == 1 and ast.dump(target.body[0]) == ast.dump(expected[0])


# colour mapping for mutant status labels in terminal output
_MUTANT_COLOURS = {
    "SURVIVED": "red",
    "KILLED": "green",
    "NO_TESTS": "yellow",
    "CRASH": "yellow",
    "ERROR": "yellow",
    "INVALID": "yellow",
}


def _print_mutant_result(func_name: str, mutant: str, status: str) -> None:
    """Print a colour-coded one-line summary of a mutant outcome."""
    # map the status string to a colour (default white for unknown)
    colour = _MUTANT_COLOURS.get(status, "white")
    console.print(f"  [{colour}]{status}[/] [dim]{func_name}[/] → {mutant}")


def _print_snapshot_info(project_dir: Path) -> None:
    """Print snapshot storage details for *project_dir*."""
    total_bytes, count = get_snapshot_storage_info(project_dir)
    snapshots_root = get_snapshot_dir(project_dir, "")
    console.print(f"\n[dim]Snapshot storage: {snapshots_root}[/dim]")
    console.print(f"[dim]{_format_bytes(total_bytes)} across {count} run(s)[/dim]")
    if count > 1:
        console.print("[dim]Run --revert to restore and clean up old snapshots.[/dim]")
    console.print()


def _format_bytes(n: int) -> str:
    """Return a human-friendly size string."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.0f} KB"
    return f"{n} B"


def _resolve_output_path(project_dir: Path, output: Path | None) -> Path:
    """Resolve report output relative to the project being analysed."""
    # Keeping relative paths inside the target project makes CLI behavior
    # independent of the directory from which PseudoSnake was launched.
    if output is None:
        return project_dir / "output" / "output.json"
    if output.is_absolute():
        return output
    return project_dir / output


# cli commands


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    project_dir: Path = typer.Option(
        None,
        "--project-dir",
        help="Root directory of the project to analyse.",
    ),
    test_command: str = typer.Option(
        None,
        "--test-command",
        help="Command used to run the project's test suite (e.g. 'pytest tests/').",
    ),
    file: Path | None = typer.Option(
        None,
        "--file",
        help="Restrict analysis to a single Python source file.",
    ),
    source_dir: Path | None = typer.Option(
        None,
        "--source-dir",
        help="Restrict recursive discovery to a source directory under project root.",
    ),
    num_test_runs: int = typer.Option(
        5,
        "--num-test-runs",
        min=1,
        help="Number of repeated full test-suite runs for baseline and each mutant.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help=(
            "Path for the JSON report. Relative paths use --project-dir; "
            "defaults to output/output.json."
        ),
    ),
    test_timeout: int = typer.Option(
        300,
        "--test-timeout",
        min=1,
        help="Timeout in seconds for each individual test-suite execution.",
    ),
    reverting: bool = typer.Option(
        False,
        "--revert",
        help="Revert PseudoSnake modifications left from a previous run.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show snapshot storage details.",
    ),
) -> None:
    """Analyse a Python project for pseudo-tested functions."""
    if reverting:
        if project_dir is None:
            console.print("[red]--project-dir is required with --revert.[/red]")
            raise typer.Exit(1)
        snapshots = list_snapshots(project_dir)
        if not snapshots:
            console.print("[green]No snapshots found.[/green]")
            raise typer.Exit()
        if verbose:
            _print_snapshot_info(project_dir)
        console.print(
            f"[bold]Found {len(snapshots)} snapshot(s). Restoring from latest...[/bold]"
        )
        latest = snapshots[0]
        count = restore_snapshot(latest, project_dir)
        console.print(
            f"[green]{count} file(s) restored from snapshot {latest.name}.[/green]"
        )
        for snap in snapshots:
            try:
                cleanup_snapshot(snap)
            except Exception:
                pass
        console.print("[green]Snapshots cleaned up.[/green]")
        return

    if project_dir is None or test_command is None:
        console.print(ctx.get_help())
        raise typer.Exit()
    analyze(
        project_dir,
        test_command,
        file,
        source_dir,
        num_test_runs,
        output,
        test_timeout,
        verbose=verbose,
    )


def analyze(
    project_dir: Path,
    test_command: str,
    file: Path | None = None,
    source_dir: Path | None = None,
    num_test_runs: int = 5,
    output: Path | None = None,
    test_timeout: int = 300,
    verbose: bool = False,
) -> None:
    """Analyse a Python project for pseudo-tested functions."""
    project_dir = project_dir.resolve()

    # record the wall-clock start time for the report metadata
    start_time = datetime.now(timezone.utc)

    # file discovery — find all source + test files the tool will modify
    files = find_python_files(project_dir, file, source_dir)
    test_files = find_test_files(project_dir)
    console.print(
        f"[bold green]PseudoSnake[/] found [cyan]{len(files)}[/] source + "
        f"[cyan]{len(test_files)}[/] test file(s) to analyse."
    )

    # snapshot only source + test files — these are the only files the tool
    # modifies, so these are the only files that need a restore point
    snapshot_id = f"run_{start_time.strftime('%Y%m%d_%H%M%S')}"
    snapshot_dir = create_snapshot(files + test_files, project_dir, snapshot_id)
    console.print(
        f"[dim]Snapshot saved — {len(files) + len(test_files)} file(s).[/dim]"
    )
    if verbose:
        _print_snapshot_info(project_dir)

    # Relative output paths belong to the analysed project, not the shell's
    # current working directory. Absolute paths remain explicit overrides.
    resolved_output = _resolve_output_path(project_dir, output)
    # ensure the output directory exists
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    # phase 0: baseline validation
    console.print(
        f"[bold]Running baseline validation[/] [dim]({num_test_runs} run(s))[/dim]..."
    )
    _validate_baseline(test_command, project_dir, num_test_runs, timeout=test_timeout)
    console.print("[bold green]Baseline passed.[/bold green]")

    try:
        # phase 1: dynamic coverage
        # Keep total and per-test calls through mutation selection and report.
        console.print("[bold]Running dynamic coverage phase...[/bold]")
        coverage_map = _run_dynamic_coverage_phase(
            files, project_dir, test_command, test_files
        )
        dynamically_executed_functions = sum(
            record.covered
            for file_coverage in coverage_map.values()
            for record in file_coverage.values()
        )
        console.print(
            f"[cyan]Coverage collected for "
            f"{dynamically_executed_functions} function(s).[/cyan]"
        )

        # phase 2: mutation testing
        file_entries: list[dict[str, Any]] = []
        # progress bar with a spinner while mutants are running
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), console=console
        ) as progress:
            for file_path in files:
                # Look up the complete records for functions in this file.
                file_coverage = coverage_map.get(
                    str(file_path.relative_to(project_dir)), {}
                )
                # analyse the file — uncovered functions will be skipped
                entry = _process_file(
                    file_path,
                    project_dir,
                    test_command,
                    num_test_runs,
                    progress,
                    file_coverage,
                    timeout=test_timeout,
                )
                if entry is not None:
                    file_entries.append(entry)

        # phase 3: report
        end_time = datetime.now(timezone.utc)
        # assemble the metadata section
        metadata = build_metadata(
            start_time,
            end_time,
            project_dir,
            source_dir,
            file,
            resolved_output,
            test_command,
            num_test_runs,
            dynamically_executed_functions,
            len(files),
        )
        # build and write the final json report
        report = build_report(metadata, file_entries)
        output_report(report, resolved_output)
        console.print(f"\n[green]Report written to[/] [cyan]{resolved_output}[/]")
    finally:
        restore_snapshot(snapshot_dir, project_dir)
