"""Main entry point for the PseudoSnake CLI.

Pipeline overview
-----------------
1. **Baseline validation** — run the test suite N times on the unmodified
   project.  If it ever fails, the project is unstable; abort.

2. **Dynamic coverage** — instrument every source and test file
   with lightweight call counters, run the test suite, and record which
   functions were actually executed (and *which tests* called them).

3. **Mutation loop** — for every discovered function, generate simple return
   mutants based on the function's annotated return type.  Apply each mutant,
   run the full test suite N times, restore the file, and classify the result:

   * ``KILLED``   — tests failed → behaviour is verified.
   * ``SURVIVED`` — tests still pass → *pseudo-tested* (called but not verified).
   * ``NO_TESTS`` — no tests collected → function is untested.

4. **Report** — write a JSON report with every function, every mutant result,
   and metadata about the run.
"""

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
    collect_executed_function_keys,
    function_key,
    instrument_file_source,
    instrument_test_file,
    is_already_instrumented,
    load_dynamic_coverage_results,
)
from pseudosnake.mutate import apply_mutant, generate_mutants
from pseudosnake.report import (
    build_file_entry,
    build_function_entry,
    build_mutant_entry,
    build_report,
    output_report,
)
from pseudosnake.runner import (
    RunResult,
    classify_result,
    run_tests_repeated,
    run_tests_with_env,
)

# cli application
app = typer.Typer(
    name="pseudosnake",
    help="PseudoSnake identifies pseudo-tested statements and methods in Python packages.",
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
) -> dict[str, set[str]]:
    """Instrument source & test files, run tests, return executed-function map.

    Returns ``{relative_file_path: {executed_function_key, ...}}`` so the
    main mutation pipeline can skip functions that were never called.
    """
    # keep track of backups so we can restore everything in the finally block
    backups: dict[Path, Path] = {}
    coverage_path = (
        Path(tempfile.gettempdir())
        / f"pseudosnake_dynamic_coverage_{uuid.uuid4().hex}.json"
    )

    # find test files in the project (excluding venv, caches, etc.)
    test_files = find_test_files(project_dir)

    try:
        # instrument test files
        total_tests = 0
        for test_path in test_files:
            source = test_path.read_text(encoding="utf-8")
            if is_already_instrumented(source):
                console.print(
                    f"  [yellow]Warning: {test_path.name} already instrumented"
                    f" from a prior run — skipping[/yellow]"
                )
                continue
            test_funcs = find_functions(test_path)
            if not test_funcs:
                continue
            # back up the original test file
            backups[test_path] = backup_file(test_path)
            relative = str(test_path.relative_to(project_dir))
            # inject __ps_ctx__.append("test_name") into each test function
            instrumented = instrument_test_file(source, test_funcs, relative)
            test_path.write_text(instrumented, encoding="utf-8")
            total_tests += len(test_funcs)
            console.print(f"  Instrumented {len(test_funcs)} test(s) in {relative}")

        # instrument source files (skip any that were already instrumented as tests)
        already_instrumented = set(backups.keys())
        instrumented_count = 0
        for file_path in files:
            if file_path in already_instrumented:
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
            # back up the original source file
            backups[file_path] = backup_file(file_path)
            relative = str(file_path.relative_to(project_dir))
            # inject hit counters into each function body
            instrumented = instrument_file_source(source, functions, relative)
            file_path.write_text(instrumented, encoding="utf-8")
            instrumented_count += len(functions)
            console.print(f"  Instrumented {len(functions)} function(s) in {relative}")

        console.print(
            f"  Total: {total_tests} test(s) + "
            f"{instrumented_count} source function(s) instrumented."
        )

        # run the test suite with instrumented code
        # the env var tells each module where to flush their counter data
        run_tests_with_env(
            test_command,
            project_dir,
            extra_env={"PSEUDOSNAKE_DYN_COV_PATH": str(coverage_path)},
        )

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

        # build the executed-function map for the mutation phase
        executed_map: dict[str, set[str]] = {}
        for file_path in files:
            # re-discover functions (lines may have shifted after instrumentation)
            functions = find_functions(file_path)
            # filter: only collect functions with hit count > 0
            executed = collect_executed_function_keys(
                file_path, project_dir, functions, coverage_results
            )
            relative = str(file_path.relative_to(project_dir))
            executed_map[relative] = set(executed)
            if executed:
                console.print(
                    f"  [cyan]{len(executed)} executed function(s) in {relative}[/cyan]"
                )
            elif functions:
                console.print(
                    f"  [dim]{relative}: {len(functions)} function(s) "
                    f"instrumented, 0 executed[/dim]"
                )

        return executed_map

    finally:
        # restore every modified file to its original content
        for file_path, backup in backups.items():
            try:
                restore_file(file_path, backup)
                # verify the restore actually put back different content
                current = file_path.read_text(encoding="utf-8")
                backup_content = backup.read_text(encoding="utf-8")
                if current != backup_content:
                    console.print(
                        f"  [bold red]ERROR: restore verification failed for"
                        f" {file_path.relative_to(project_dir)}[/bold red]"
                    )
            except Exception as exc:
                console.print(
                    f"  [yellow]Warning: failed to restore"
                    f" {file_path.relative_to(project_dir)}: {exc}[/yellow]"
                )
            try:
                cleanup_backup(backup)
            except Exception:
                pass  # best-effort cleanup — don't prevent restoring other files
        try:
            coverage_path.unlink(missing_ok=True)
        except Exception:
            pass


# phase 2 — mutation testing


def _process_file(
    file_path: Path,
    project_dir: Path,
    test_command: str,
    num_test_runs: int,
    progress: Progress,
    executed_function_keys: set[str] | None = None,
    timeout: int | None = 300,
) -> dict[str, Any] | None:
    """Analyse all functions in one source file.

    When *executed_function_keys* is provided (dynamic coverage enabled),
    only functions whose key appears in the set are mutation-tested.
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
        # check if this function should be mutation-tested
        # when coverage data is available, skip functions that were never called
        covered = (
            executed_function_keys is None or function_key(f) in executed_function_keys
        )

        if covered:
            if executed_function_keys is not None:
                console.print(f"  [green]Covered[/] [dim]{f.name}[/]")
            # run all mutants for this function
            function_entries.append(
                _process_function(
                    f,
                    test_command,
                    num_test_runs,
                    project_dir,
                    progress,
                    timeout=timeout,
                )
            )
        else:
            # function was never executed — skip mutation, mark as uncovered
            console.print(f"  [dim]Not covered[/] [dim]{f.name}[/] - skipping")
            function_entries.append(build_uncovered_entry(f))

    return build_file_entry(file_path, project_dir, function_entries)


def _process_function(
    func_info: FunctionInfo,
    test_command: str,
    num_test_runs: int,
    project_dir: Path,
    progress: Progress,
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

    return build_function_entry(func_info, mutant_entries)


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
    # replace the function body with a single return statement
    mutated_lines = apply_mutant(source_lines, func_info, mutant)
    mutated_source = "".join(mutated_lines)
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
    """Check that the mutated source contains the expected mutant in the target function."""
    lines = mutated_source.split("\n")
    # the body starts at body_start_line (1-indexed) and the replacement
    # occupies exactly one line (the mutant statement)
    body_line_idx = func_info.body_start_line - 1
    if body_line_idx >= len(lines):
        return False
    actual = lines[body_line_idx].strip()
    expected = mutant.strip()
    return actual == expected


# colour mapping for mutant status labels in terminal output
_MUTANT_COLOURS = {
    "SURVIVED": "red",
    "KILLED": "green",
    "NO_TESTS": "yellow",
    "CRASH": "yellow",
}


def _print_mutant_result(func_name: str, mutant: str, status: str) -> None:
    """Print a colour-coded one-line summary of a mutant outcome."""
    # map the status string to a colour (default white for unknown)
    colour = _MUTANT_COLOURS.get(status, "white")
    console.print(f"  [{colour}]{status}[/] [dim]{func_name}[/] → {mutant}")


# cli entry points


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    project_dir: Path = typer.Option(
        None,
        "--project-dir",
        help="Root directory of the project.",
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
        help="Path for the JSON report. Defaults to <project-dir>/output/output.json.",
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
) -> None:
    """Analyse a Python project for pseudo-tested functions."""
    if reverting:
        if project_dir is None:
            console.print("[red]--project-dir is required with --revert.[/red]")
            raise typer.Exit(1)
        snapshots = list_snapshots()
        if not snapshots:
            console.print("[green]No snapshots found.[/green]")
            raise typer.Exit()
        console.print(
            f"[bold]Found {len(snapshots)} snapshot(s)."
            f" Restoring from latest...[/bold]"
        )
        latest = snapshots[0]
        count = restore_snapshot(latest, project_dir)
        console.print(
            f"[green]{count} file(s) restored from snapshot"
            f" {latest.name}.[/green]"
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
    analyze(project_dir, test_command, file, source_dir, num_test_runs, output, test_timeout)


def analyze(
    project_dir: Path,
    test_command: str,
    file: Path | None = None,
    source_dir: Path | None = None,
    num_test_runs: int = 5,
    output: Path | None = None,
    test_timeout: int = 300,
) -> None:
    """Analyse a Python project for pseudo-tested functions."""
    # record the wall-clock start time for the report metadata
    start_time = datetime.now(timezone.utc)

    # default output path
    # if no --output flag was given, write to <project-dir>/output/output.json
    resolved_output = (
        output if output is not None else project_dir / "output" / "output.json"
    )
    # ensure the output directory exists
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    # phase 0: baseline validation
    console.print(
        "[bold]Running baseline validation[/] [dim]({num} run(s))[/dim]...".format(
            num=num_test_runs
        )
    )
    _validate_baseline(test_command, project_dir, num_test_runs, timeout=test_timeout)
    console.print("[bold green]Baseline passed.[/bold green]")

    # file discovery
    # find all python source files in the project (skipping tests)
    files = find_python_files(project_dir, file, source_dir)
    console.print(
        f"[bold green]PseudoSnake[/] found [cyan]{len(files)}[/] file(s) to analyse."
    )

    # snapshot all source + test files so they can be restored on crash
    test_files_snapshot = find_test_files(project_dir)
    snapshot_id = f"run_{start_time.strftime('%Y%m%d_%H%M%S')}"
    snapshot_dir = create_snapshot(
        files + test_files_snapshot, project_dir, snapshot_id
    )

    try:
        # phase 1: dynamic coverage
        # coverage_map: {relative_file_path: {executed_function_key, ...}}
        console.print("[bold]Running dynamic coverage phase...[/bold]")
        coverage_map = _run_dynamic_coverage_phase(files, project_dir, test_command)
        dynamically_executed_functions = sum(len(v) for v in coverage_map.values())
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
                # look up which functions in this file were covered (if any)
                exe_keys = coverage_map.get(
                    str(file_path.relative_to(project_dir))
                )
                # analyse the file — uncovered functions will be skipped
                entry = _process_file(
                    file_path,
                    project_dir,
                    test_command,
                    num_test_runs,
                    progress,
                    exe_keys,
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
        cleanup_snapshot(snapshot_dir)
