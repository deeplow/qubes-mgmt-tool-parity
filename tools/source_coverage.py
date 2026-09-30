#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 deeplow
# SPDX-License-Identifier: Apache-2.0
"""List source functions that no requirement traces to.

StrictDoc's Source coverage screen gives per-file line and function percentages
but not *which* functions are untraced. This builds the same traceability index
as `strictdoc export` and names them, using StrictDoc's own test
(FileTraceabilityIndex.calculate_code_coverage_and_sort_nodes): a function is
covered only when it lies entirely inside a range merged from the requirement
markers on its file.

Run from the repo root:

    uv run python tools/source_coverage.py [--prefix qubes-terraform/] [--summary]

Always exits 0: this is a report, not a gate. tools/audit_spec.py is the gate.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parent.parent

# strictdoc_config.py names tools.parity_matrix, so the repo root must be
# importable, exactly as it is when strictdoc runs from there.
sys.path.insert(0, str(REPO))

from strictdoc.commands.export_config import ExportCommandConfig  # noqa: E402
from strictdoc.core.project_config import ProjectConfigLoader  # noqa: E402
from strictdoc.core.traceability_index import TraceabilityIndex  # noqa: E402
from strictdoc.core.traceability_index_builder import (  # noqa: E402
    TraceabilityIndexBuilder,
)
from strictdoc.helpers.parallelizer import Parallelizer  # noqa: E402

# First matching prefix wins. Files under --prefix that match none land in
# "other". Vendored code is kept apart: only the parts the provider calls
# carry requirements, so its untraced functions are expected, not gaps.
GROUPS: Tuple[Tuple[str, str], ...] = (
    ("vendored qubes-ansible", "qubes-terraform/qubes_provider/utils/qubes_ansible/"),
    ("tests", "qubes-terraform/tests/"),
    ("provider", "qubes-terraform/"),
)


@dataclass
class FileReport:
    path: str
    line_coverage: float
    functions_total: int
    untraced: List[Tuple[int, str]] = field(default_factory=list)

    @property
    def functions_covered(self) -> int:
        return self.functions_total - len(self.untraced)


def build_index() -> TraceabilityIndex:
    """The index `strictdoc export .` builds, without exporting anything."""
    export_config = ExportCommandConfig(
        debug=False,
        command="export",
        input_paths=[str(REPO)],
        output_dir=None,
        config=None,
        project_title=None,
        formats=["html"],
        fields=["UID"],
        generate_bundle_document=False,
        no_parallelization=True,
        enable_mathjax=False,
        included_documents=False,
        filter_nodes=None,
        reqif_profile=None,
        reqif_multiline_is_xhtml=False,
        reqif_enable_mid=False,
        view=None,
        generate_diff_git=None,
        generate_diff_dirs=None,
        chromedriver=None,
    )
    export_config.validate()
    project_config = ProjectConfigLoader.load_using_export_config(export_config)
    # StrictDoc prints a line per file it reads; keep it only if the build fails.
    chatter = io.StringIO()
    try:
        with contextlib.redirect_stdout(chatter):
            return TraceabilityIndexBuilder.create(
                project_config=project_config,
                parallelizer=Parallelizer.create(False),
            )
    except BaseException:
        sys.stdout.write(chatter.getvalue())
        raise


def _rel_path(key: str, info: object) -> str:
    source_file = getattr(info, "source_file", None)
    rel = getattr(source_file, "in_doctree_source_file_rel_path_posix", None)
    if rel:
        return rel
    return Path(os.path.relpath(key, REPO)).as_posix()


def collect(index: TraceabilityIndex, prefix: str) -> List[FileReport]:
    file_index = index.get_file_traceability_index()
    reports = []
    for key, info in file_index.map_paths_to_source_file_traceability_info.items():
        path = _rel_path(key, info)
        if not path.startswith(prefix):
            continue
        untraced = sorted(
            (function_.line_begin, function_.display_name or function_.name)
            for function_ in info.functions
            if not any(
                begin <= function_.line_begin and function_.line_end <= end
                for begin, end in info.merged_ranges
            )
        )
        reports.append(
            FileReport(
                path=path,
                line_coverage=info.get_coverage(),
                functions_total=len(info.functions),
                untraced=untraced,
            )
        )
    return sorted(reports, key=lambda r: r.path)


def group_of(path: str) -> str:
    for label, group_prefix in GROUPS:
        if path.startswith(group_prefix):
            return label
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--prefix",
        default="qubes-terraform/",
        help="report only files under this repo-relative path (default: %(default)s)",
    )
    ap.add_argument(
        "--summary",
        action="store_true",
        help="per-file totals only, without the untraced function names",
    )
    args = ap.parse_args()

    os.chdir(REPO)
    reports = collect(build_index(), args.prefix)
    if not reports:
        print(f"source coverage: no indexed files under {args.prefix}")
        return 0

    by_group: Dict[str, List[FileReport]] = {}
    for report in reports:
        by_group.setdefault(group_of(report.path), []).append(report)

    order = [label for label, _ in GROUPS] + ["other"]
    for label in order:
        group = by_group.get(label)
        if not group:
            continue
        total = sum(r.functions_total for r in group)
        covered = sum(r.functions_covered for r in group)
        print(f"\n{label.upper()}: {covered}/{total} functions traced")
        for report in group:
            print(
                f"  {report.path}  lines {report.line_coverage:.1f}%  "
                f"functions {report.functions_covered}/{report.functions_total}"
            )
            if args.summary:
                continue
            for line, name in report.untraced:
                print(f"      {line:>5}  {name}")

    untraced = sum(len(r.untraced) for r in reports)
    print(f"\nsource coverage: {untraced} untraced function(s) under {args.prefix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
