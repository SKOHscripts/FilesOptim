"""Command line interface."""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from filesoptim import __version__
from filesoptim.clean import Cleaner
from filesoptim.config import (
    DEFAULT_CONFIG_TOML,
    OPTIMIZE_TYPES,
    SORT_CATEGORIES,
    Config,
    ConfigError,
    default_config_path,
    dump_config,
    load_config,
    state_dir,
    validate,
)
from filesoptim.doctor import run_doctor
from filesoptim.dupes import (
    ACTIONS,
    KEEP_STRATEGIES,
    apply_actions,
    choose_keeper,
    find_duplicates,
    render_groups,
)
from filesoptim.fsutils import WalkOptions
from filesoptim.optimize import Engine
from filesoptim.report import build_report, render_report, report_to_dict
from filesoptim.scan import big_files, broken_links, empty_dirs, protected_dirs, topmost
from filesoptim.sorting.executor import (
    SortExecutor,
    iter_undo,
    list_journals,
    mark_undone,
    plan_undo,
    read_journal,
)
from filesoptim.sorting.planner import Planner, SortOptions, export_plan, preview
from filesoptim.state import StateDB
from filesoptim.tools import Tools, install_hint
from filesoptim.ui import Console, human_size, parse_size

EPILOG = """\
examples:
  filesoptim optimize ~/Pictures --estimate-only   exact estimate, nothing changed
  filesoptim optimize ~/Pictures ~/Videos          estimate, confirm, then optimise
  filesoptim sort ~/Downloads/Photos --dest ~/Pictures --rename --fix-dates --tag
  filesoptim undo                                  revert the last sort
  filesoptim clean --dry-run                       what cleaning would free
  filesoptim dupes ~ --action trash --keep oldest
  filesoptim report ~ --estimate --dupes
"""

Handler = Callable[[argparse.Namespace, Config, Console, Tools], int]


class UsageError(Exception):
    """Invalid command line arguments (exit code 2)."""


def split_list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def existing_paths(values: Sequence[str]) -> list[Path]:
    # Absolute paths: a file named "-x.pdf" can never be mistaken for an option by a tool.
    paths = [Path(v).expanduser().absolute() for v in values]
    missing = [str(p) for p in paths if not (p.exists() or p.is_symlink())]
    if missing:
        raise UsageError(f"not found: {', '.join(missing)}")
    return paths


def walk_options(args: argparse.Namespace, config: Config) -> WalkOptions:
    walk = WalkOptions.from_config(
        config.general, include_hidden=args.include_hidden, extra_exclude=args.exclude
    )
    if args.cross_filesystems:
        walk.one_file_system = False
    return walk


# ------------------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------------------
def cmd_optimize(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    paths = existing_paths(args.paths)
    opt, video = config.optimize, config.video
    if args.types:
        opt.types = split_list(args.types)
    overrides: list[tuple[Any, str, Any]] = [
        (opt, "min_saving_percent", args.min_saving),
        (opt, "keep_originals", args.keep_originals),
        (opt, "backup_dir", args.backup_dir),
        (opt, "jobs", args.jobs),
        (opt, "jpeg_progressive", args.jpeg_progressive),
        (video, "codec", args.codec),
        (video, "crf", args.crf),
        (video, "preset", args.preset),
        (video, "metric", args.metric),
        (video, "min_ssim", args.min_ssim),
        (video, "min_vmaf", args.min_vmaf),
        (video, "min_saving_percent", args.video_min_saving),
        (config.pdf, "mode", args.pdf_mode),
        (config.general, "min_age_seconds", args.min_age),
    ]
    for section, name, value in overrides:
        if value is not None:
            setattr(section, name, value)
    validate(config)
    with StateDB(state_dir() / "state.sqlite") as state:
        engine = Engine(config, tools, console, state, force=args.force,
                        walk=walk_options(args, config))
        summary = engine.run(paths, estimate_only=args.estimate_only, assume_yes=args.yes)
    return 1 if summary.failed else 0


def cmd_sort(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    source = existing_paths([args.source])[0]
    if not source.is_dir():
        raise UsageError(f"{source} is not a folder")
    only = split_list(args.only)
    unknown = set(only) - set(SORT_CATEGORIES)
    if unknown:
        raise UsageError(f"unknown categories: {', '.join(sorted(unknown))} "
                         f"(choose among {', '.join(SORT_CATEGORIES)})")
    if args.no_mtime:
        config.sort.use_mtime = False
    for rule in args.template:
        category, sep, template = rule.partition("=")
        if not sep or category.strip() not in SORT_CATEGORIES:
            raise UsageError(f"--template expects CATEGORY=TEMPLATE with CATEGORY among "
                             f"{', '.join(SORT_CATEGORIES)} (got {rule!r})")
        config.sort.templates[category.strip()] = template.strip()
    options = SortOptions(
        source=source,
        destination=Path(args.dest).expanduser() if args.dest else source,
        mode="copy" if args.copy else config.sort.mode,
        rename=args.rename,
        fix_dates=args.fix_dates,
        tag=args.tag or bool(args.tags),
        extra_tags=split_list(args.tags),
        only=only,
        leave_sorted=[*config.sort.leave_sorted, *args.leave_sorted],
        set_mtime=args.set_mtime,
        prune_empty=args.prune_empty,
        recursive=not args.flat,
    )
    if (options.fix_dates or options.tag) and not tools.has("exiftool"):
        console.warn("exiftool is missing: dates and keywords cannot be written "
                      f"({install_hint(['exiftool'], tools)}).")
    plan = Planner(config, tools).plan(options)
    preview(plan, console, limit=None if args.show_all else 30)
    if args.export_plan:
        export_plan(plan, Path(args.export_plan))
        console.info(f"Full plan written to {args.export_plan}")
    if not plan.ops:
        console.info("Nothing to do.")
        return 0
    if args.dry_run:
        return 0
    if not console.confirm(f"Apply this plan ({len(plan.ops)} operation(s))?",
                           assume_yes=args.yes):
        return 0
    result = SortExecutor(tools, console).execute(plan)
    console.success(f"{result.done} operation(s) done.")
    console.info(f"To revert: filesoptim undo {result.journal}")
    return 1 if result.failures else 0


def cmd_undo(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    if args.list:
        rows = []
        for path in list_journals(include_undone=True):
            header, entries = read_journal(path)
            rows.append([path.name, header.get("created", "?"), header.get("source", "?"),
                         len(entries)])
        console.table(["journal", "created", "source", "entries"], rows)
        return 0
    if args.journal:
        journal = existing_paths([args.journal])[0]
    else:
        journals = list_journals()
        if not journals:
            console.info("No sorting to undo.")
            return 0
        journal = journals[-1]
    header, entries = read_journal(journal)
    base = header.get("source")
    steps = plan_undo(entries, Path(base) if base else None)
    console.heading(f"Undo {journal.name} (sorted on {header.get('created', '?')})")
    for step in steps:
        if step.blocked:
            console.warn(f"{step.description}: {step.blocked}")
        else:
            console.info(f"  {step.description}")
    if not steps or args.dry_run:
        return 0
    if not console.confirm(f"Undo these {len(steps)} step(s)?", assume_yes=args.yes):
        return 0
    errors = 0
    for step, error in iter_undo(steps, tools):
        if error and not step.blocked:
            errors += 1
            console.error(f"{step.description}: {error}")
    if errors:
        console.warn(f"{errors} step(s) failed: the journal is kept, fix the cause and run "
                     "the undo again.")
        return 1
    mark_undone(journal)
    console.success("Undo finished.")
    return 0


def cmd_clean(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    cleaner = Cleaner(config.clean, console)
    if args.list:
        cleaner.list_targets()
        return 0
    _, errors = cleaner.run(split_list(args.only), system=args.system, assume_yes=args.yes,
                            dry_run=args.dry_run)
    return 1 if errors else 0


def cmd_dupes(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    paths = existing_paths(args.paths)
    groups = find_duplicates(paths, walk_options(args, config), min_size=parse_size(args.min_size))
    prefer = [Path(p).expanduser().absolute() for p in args.prefer]
    for group in groups:
        group.keep = choose_keeper(group, args.keep, prefer)
    if args.json:
        print(json.dumps([{"size": g.size, "keep": str(g.keep),
                           "remove": [str(p) for p in g.remove], "wasted": g.wasted}
                          for g in groups], indent=2))
    else:
        render_groups(groups, console, limit=None if args.show_all else 20)
    if args.action == "report" or not groups or args.dry_run:
        return 0
    copies = sum(len(g.remove) for g in groups)
    wasted = human_size(sum(g.wasted for g in groups))
    if not console.confirm(f"{args.action} {copies} duplicate(s) to free {wasted}?",
                           assume_yes=args.yes):
        return 0
    freed, errors = apply_actions(groups, args.action, console)
    console.success(f"{human_size(freed)} freed.")
    return 1 if errors else 0


def cmd_bigfiles(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    paths = existing_paths(args.paths)
    found = big_files(paths, walk_options(args, config), top=args.top,
                      min_size=parse_size(args.min_size))
    if args.json:
        print(json.dumps([{"size": s, "path": str(p)} for s, p in found], indent=2))
    else:
        console.table(["size", "file"], [[human_size(s), str(p)] for s, p in found],
                      align="rl", max_width=120)
    return 0


def cmd_emptydirs(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    paths = existing_paths(args.paths)
    found = empty_dirs(paths, walk_options(args, config), protected_dirs(config))
    shown = topmost(found)
    console.heading(f"Empty folders: {len(shown)} (with sub-folders: {len(found)})")
    for path in shown:
        console.info(f"  {path}")
    if not args.delete or not found or args.dry_run:
        return 0
    if not console.confirm(f"Remove these {len(found)} empty folder(s)?", assume_yes=args.yes):
        return 0
    errors = 0
    for path in found:  # deepest first; rmdir refuses anything that is not empty
        try:
            path.rmdir()
        except OSError as exc:
            errors += 1
            console.error(f"{path}: {exc}")
    console.success(f"{len(found) - errors} folder(s) removed.")
    return 1 if errors else 0


def cmd_brokenlinks(args: argparse.Namespace, config: Config, console: Console,
                    tools: Tools) -> int:
    paths = existing_paths(args.paths)
    found = broken_links(paths, walk_options(args, config))
    console.heading(f"Broken symbolic links: {len(found)}")
    for path, target in found:
        console.info(f"  {path} → {target}")
    if not args.delete or not found or args.dry_run:
        return 0
    if not console.confirm(f"Delete these {len(found)} broken link(s)?", assume_yes=args.yes):
        return 0
    removed = 0
    for path, _ in found:
        if path.is_symlink() and not path.exists():  # still broken?
            path.unlink()
            removed += 1
    console.success(f"{removed} link(s) deleted.")
    return 0


def cmd_report(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    paths = existing_paths(args.paths or [str(Path.home())])
    walk = walk_options(args, config)
    report = build_report(paths, walk, top=args.top)
    estimate = duplicates = None
    inner = Console(io.StringIO(), color=False, interactive=False) if args.json else console
    if not args.json:
        render_report(report, console, top=args.top)
    if args.estimate:
        with StateDB(state_dir() / "state.sqlite") as state:
            estimate = Engine(config, tools, inner, state, walk=walk).run(
                paths, estimate_only=True)
    if args.dupes:
        duplicates = find_duplicates(paths, walk)
        for group in duplicates:
            group.keep = choose_keeper(group, "oldest")
        if not args.json:
            render_groups(duplicates, console, limit=5)
    if args.json:
        print(json.dumps(report_to_dict(report, estimate=estimate, duplicates=duplicates),
                         indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    return run_doctor(config, tools, console, args.config or default_config_path())


def cmd_config(args: argparse.Namespace, config: Config, console: Console, tools: Tools) -> int:
    path = args.config or default_config_path()
    if args.path:
        print(path)
        return 0
    if args.init:
        if path.exists() and not args.force:
            raise UsageError(f"{path} already exists (use --force to overwrite)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        console.success(f"Configuration written to {path}")
        return 0
    print(dump_config(config))
    return 0


# ------------------------------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------------------------------
def _common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="show details")
    common.add_argument("-q", "--quiet", action="store_true", help="only warnings and errors")
    common.add_argument("--no-color", action="store_true", help="disable colours")
    common.add_argument("--config", type=Path, help="configuration file to use")
    return common


def _walk_parser() -> argparse.ArgumentParser:
    walk = argparse.ArgumentParser(add_help=False)
    walk.add_argument("--include-hidden", action="store_true",
                      help="also visit hidden files and folders")
    walk.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                      help="skip names matching this glob pattern (repeatable)")
    walk.add_argument("--cross-filesystems", action="store_true",
                      help="enter other mounted filesystems")
    return walk


def _confirm_parser() -> argparse.ArgumentParser:
    confirm = argparse.ArgumentParser(add_help=False)
    confirm.add_argument("-y", "--yes", action="store_true",
                         help="apply without asking (the preview is still shown)")
    confirm.add_argument("-n", "--dry-run", action="store_true",
                         help="only show what would be done")
    return confirm


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="filesoptim",
        description="Keep a Linux computer lean and tidy: lossless optimisation, sorting, "
                    "tagging, cleaning, duplicates and reports. Every command previews or "
                    "estimates before changing anything.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    common, walk, confirm = _common_parser(), _walk_parser(), _confirm_parser()

    def add(name: str, handler: Handler, help_text: str,
            parents: Sequence[argparse.ArgumentParser] = ()) -> argparse.ArgumentParser:
        command = sub.add_parser(name, help=help_text, description=help_text,
                                 parents=[common, *parents])
        command.set_defaults(handler=handler)
        return command

    p = add("optimize", cmd_optimize,
            "estimate precisely, then optimise images, PDF and videos without visible loss",
            [walk, confirm])
    p.add_argument("paths", nargs="+", metavar="PATH")
    p.add_argument("-t", "--types", help=f"comma list among {', '.join(OPTIMIZE_TYPES)}")
    p.add_argument("--estimate-only", action="store_true",
                   help="stop after the estimate (same as --dry-run)")
    p.add_argument("--force", action="store_true", help="ignore the memory of processed files")
    p.add_argument("-j", "--jobs", type=int, help="parallel workers for images/PDF")
    p.add_argument("--min-saving", type=float, metavar="PCT", help="lossless minimum gain")
    p.add_argument("--keep-originals", choices=("auto", "never", "trash", "backup"))
    p.add_argument("--backup-dir", help="where originals go with --keep-originals backup")
    p.add_argument("--jpeg-progressive", action=argparse.BooleanOptionalAction, default=None,
                   help="lossless conversion to progressive JPEG")
    p.add_argument("--codec", choices=("hevc", "av1"), help="video codec")
    p.add_argument("--crf", type=int, help="video quality (lower = better, 0 = default)")
    p.add_argument("--preset", help="encoder preset (speed/size trade-off)")
    p.add_argument("--metric", choices=("ssim", "vmaf"), help="video quality metric")
    p.add_argument("--min-ssim", type=float, help="minimum SSIM (default 0.98)")
    p.add_argument("--min-vmaf", type=float, help="minimum VMAF (default 95)")
    p.add_argument("--video-min-saving", type=float, metavar="PCT",
                   help="minimum video gain (default 20)")
    p.add_argument("--pdf-mode",
                   choices=("lossless", "printer", "ebook", "prepress", "screen", "default"))
    p.add_argument("--min-age", type=int, metavar="SECONDS",
                   help="skip files modified more recently than this")

    p = add("sort", cmd_sort, "sort files into folders (Year/Month...), rename and tag them",
            [confirm])
    p.add_argument("source", metavar="SOURCE")
    p.add_argument("--dest", help="destination root (default: SOURCE itself)")
    p.add_argument("--copy", action="store_true", help="copy instead of moving")
    p.add_argument("--rename", action="store_true", help="rename from the date / music tags")
    p.add_argument("--fix-dates", action="store_true",
                   help="write the missing date (found in the file name) into the metadata")
    p.add_argument("--tag", action="store_true", help="add XMP keywords")
    p.add_argument("--tags", help="extra keywords, comma separated (implies --tag)")
    p.add_argument("--only", help=f"comma list among {', '.join(SORT_CATEGORIES)}")
    p.add_argument("--leave-sorted", action="append", default=[], metavar="PATTERN",
                   help="never touch files in folders matching this pattern, relative to "
                        "SOURCE: * = one folder, ** = any depth, {year}, {month} "
                        "(e.g. \"{year}/*\" for Year/Event); repeatable")
    p.add_argument("--template", action="append", default=[], metavar="CATEGORY=TEMPLATE",
                   help="destination folder for a category, e.g. "
                        "\"image={year}/{year}-{month}\" (repeatable)")
    p.add_argument("--no-mtime", action="store_true",
                   help="never date a file from its modification time")
    p.add_argument("--set-mtime", action="store_true",
                   help="set the file modification time to the date found")
    p.add_argument("--prune-empty", action="store_true",
                   help="remove source folders left empty")
    p.add_argument("--flat", action="store_true", help="do not look into sub-folders")
    p.add_argument("--show-all", action="store_true", help="list every planned operation")
    p.add_argument("--export-plan", metavar="FILE", help="save the full plan as JSON")

    p = add("undo", cmd_undo, "revert a sort using its journal", [confirm])
    p.add_argument("journal", nargs="?", help="journal file (default: the latest)")
    p.add_argument("--list", action="store_true", help="list the journals")

    p = add("clean", cmd_clean, "clean caches, old trash, logs (exact size shown first)",
            [confirm])
    p.add_argument("--only", help="comma list of targets (see --list)")
    p.add_argument("--system", action="store_true",
                   help="also clean system targets (package caches, journal; needs root)")
    p.add_argument("--list", action="store_true", help="list the available targets")

    p = add("dupes", cmd_dupes, "find (and remove or link) duplicate files", [walk, confirm])
    p.add_argument("paths", nargs="+", metavar="PATH")
    p.add_argument("--min-size", default="1", help="ignore smaller files (e.g. 100k, 1M)")
    p.add_argument("--action", choices=ACTIONS, default="report")
    p.add_argument("--keep", choices=KEEP_STRATEGIES, default="oldest",
                   help="which copy is kept")
    p.add_argument("--prefer", action="append", default=[], metavar="DIR",
                   help="keep copies located in this folder (repeatable)")
    p.add_argument("--show-all", action="store_true")
    p.add_argument("--json", action="store_true")

    p = add("bigfiles", cmd_bigfiles, "list the biggest files", [walk])
    p.add_argument("paths", nargs="+", metavar="PATH")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-size", default="0")
    p.add_argument("--json", action="store_true")

    p = add("emptydirs", cmd_emptydirs, "find (and remove) empty folders", [walk, confirm])
    p.add_argument("paths", nargs="+", metavar="PATH")
    p.add_argument("--delete", action="store_true")

    p = add("brokenlinks", cmd_brokenlinks, "find (and delete) broken symbolic links",
            [walk, confirm])
    p.add_argument("paths", nargs="+", metavar="PATH")
    p.add_argument("--delete", action="store_true")

    p = add("report", cmd_report, "disk usage report and recoverable space", [walk])
    p.add_argument("paths", nargs="*", metavar="PATH", help="default: your home folder")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--estimate", action="store_true",
                   help="exact optimisation estimate (can be slow for videos)")
    p.add_argument("--dupes", action="store_true", help="include duplicates")
    p.add_argument("--json", action="store_true")

    add("doctor", cmd_doctor, "check installed tools and features")

    p = add("config", cmd_config, "show the effective configuration or create the file")
    p.add_argument("--init", action="store_true", help="write a commented default file")
    p.add_argument("--force", action="store_true", help="overwrite with --init")
    p.add_argument("--path", action="store_true", help="print the configuration file path")
    return parser


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None, *, tools: Tools | None = None,
         console: Console | None = None) -> int:
    # Closing the terminal (SIGHUP) or `kill` (SIGTERM) stop cleanly, like Ctrl+C: temporary
    # files are removed and running tools are stopped.
    previous = {sig: signal.signal(sig, _interrupt) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        return _main(argv, tools=tools, console=console)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _main(argv: Sequence[str] | None, *, tools: Tools | None, console: Console | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    if console is None:
        console = Console(color=False if args.no_color else None, quiet=args.quiet,
                          verbose=args.verbose)
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="  $ %(message)s", stream=sys.stderr)
    try:
        config = load_config(args.config)
        if getattr(args, "estimate_only", False) or getattr(args, "dry_run", False):
            args.estimate_only = args.dry_run = True
        return int(args.handler(args, config, console, tools or Tools()))
    except (ConfigError, UsageError, ValueError) as exc:
        console.error(str(exc))
        return 2
    except KeyboardInterrupt:
        console.error("Interrupted: work stopped and temporary files removed. Originals are "
                      "only ever replaced atomically: no file was left half-written.")
        return 130
    except BrokenPipeError:  # e.g. piped into `head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
