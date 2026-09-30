# SPDX-FileCopyrightText: 2026 deeplow
# SPDX-License-Identifier: Apache-2.0

"""
Render a two-level coverage matrix as StrictDoc's project statistics screen.

Rows are parent requirements, columns are the values of a MultipleChoice field
on them, and each cell holds one chip per child requirement that refines that
row for that column, coloured by the child's status field. A column outside a
row's declared set is drawn as out of scope, which is deliberately not a gap.

Nothing about a particular project is hardcoded. The structure is inferred from
the project's own grammar (see MatrixConfig and _resolve): the field declared
MultipleChoice on one node type and SingleChoice on another identifies the row
type, the cell type and the column field in one go, because that narrowing is
exactly the parent/child relationship a coverage matrix draws. Only the mapping
from status values to colours has to be configured, since neither severity
order nor colour is derivable from a grammar.

Wired in via the `statistics_generator` key in strictdoc_config.py. The page
is rendered by StrictDoc: this module only produces Metric and MetricSection
values and hands them to ProjectStatisticsViewObject, so nav, header, footer
and stylesheets come from StrictDoc's own screen template. Only the grid is
custom HTML, styled by tools/parity_matrix.css via `custom_css_path`.
"""

import re
from collections import OrderedDict
from itertools import groupby
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from markupsafe import Markup, escape

from strictdoc.api import (
    HTMLTemplates,
    LinkRenderer,
    Metric,
    MetricSection,
    ProjectConfig,
    ProjectStatisticsViewObject,
    SDocDocumentIterator,
    SDocNode,
    TraceabilityIndex,
)

# StrictDoc's own vocabulary, not this project's.
SECTION_NODE_TYPE = "SECTION"
PARENT_RELATION = "Parent"
TYPE_STRING = "String"
TYPE_SINGLE_CHOICE = "SingleChoice"
TYPE_MULTIPLE_CHOICE = "MultipleChoice"

OUT_OF_SCOPE_CLASS = "out"
UNKNOWN_STATUS_CLASS = "none"

# Narrower segments clip their label into noise; the tooltip still carries it.
_BAR_LABEL_MIN_PERCENT = 5


class MatrixConfigError(Exception):
    """Raised when the matrix cannot be derived from the grammar.

    The generator runs inside the export, so failing here is deliberate: a
    half-inferred matrix would render as quietly wrong rather than absent.
    """


@dataclass(frozen=True)
class MatrixConfig:
    """
    Structural fields are inferred from the grammar when left as None.

    Set one explicitly only to disambiguate, or to override the inference.
    """

    row_node_type: Optional[str] = None
    cell_node_type: Optional[str] = None
    column_field: Optional[str] = None
    status_field: Optional[str] = None
    # Free-text field shown in chip tooltips. Inferred when None; "" disables.
    note_field: Optional[str] = None
    # Row filter: UIDs of parent nodes offered as a selector (labelled with
    # their titles); choosing one keeps only the rows refining it.
    # Empty: no selector.
    row_filter_parents: Tuple[str, ...] = ()
    # Tabs (with column groups): row-document titles in the wanted order,
    # others following, and a prefix to drop from the tab labels.
    tab_order: Tuple[str, ...] = ()
    tab_label_prefix: str = ""
    # Label of the row filter's "every row" option, and of its filter group.
    row_filter_all_label: str = "All"
    row_filter_label: str = "Filter"

    # Presentation. Not inferable.
    row_header: str = "Requirement"
    title: str = "Parity matrix"
    # Worst first. The grammar decides which statuses exist; this maps them to
    # a CSS class in tools/parity_matrix.css. Entries not present in the
    # grammar are ignored, so this default suits any subset of these.
    status_classes: Tuple[Tuple[str, str], ...] = (
        ("Not Implemented", "none"),
        ("Partial", "partial"),
        ("Workaround", "workaround"),
        ("Implemented", "full"),
    )


@dataclass(frozen=True)
class _Resolved:
    """A MatrixConfig with every structural value pinned down."""

    row_node_type: str
    cell_node_type: str
    column_field: str
    status_field: str
    note_field: str
    columns: Tuple[str, ...]
    statuses: Tuple[str, ...]  # worst first
    status_class: Dict[str, str]
    status_rank: Dict[str, int]
    row_header: str
    title: str
    row_filter_parents: Tuple[str, ...] = ()
    tab_order: Tuple[str, ...] = ()
    tab_label_prefix: str = ""
    row_filter_all_label: str = "All"
    row_filter_label: str = "Filter"


def _grammar_fields(
    traceability_index: TraceabilityIndex,
) -> Dict[str, Dict[str, object]]:
    """node type -> field title -> grammar field, merged across documents.

    Documents that import a shared grammar all report the same elements, so
    merging is a no-op for them; a document with its own grammar simply adds
    its element types.
    """
    index: Dict[str, Dict[str, object]] = {}
    for document_ in traceability_index.document_tree.document_list:
        grammar = getattr(document_, "grammar", None)
        if grammar is None:
            continue
        for tag_, element_ in getattr(grammar, "elements_by_type", {}).items():
            fields_ = index.setdefault(tag_, {})
            for field_ in element_.fields:
                fields_.setdefault(field_.title, field_)
    return index


def _type_of(fields: Dict[str, object], title: str) -> Optional[str]:
    field_ = fields.get(title)
    return getattr(field_, "gef_type", None) if field_ is not None else None


def _options(fields: Dict[str, object], title: str) -> Tuple[str, ...]:
    field_ = fields.get(title)
    return tuple(getattr(field_, "options", ()) or ())


def _resolve(config: MatrixConfig, grammar: Dict[str, Dict[str, object]]) -> _Resolved:
    if not grammar:
        raise MatrixConfigError(
            "parity matrix: the project has no grammar elements to inspect."
        )

    row_type, cell_type, column_field = _infer_axes(config, grammar)
    row_fields = grammar[row_type]
    cell_fields = grammar[cell_type]

    status_field = config.status_field or _infer_unique(
        _candidates(cell_fields, row_fields, TYPE_SINGLE_CHOICE, {column_field}),
        what="status field",
        setting="status_field",
        detail=f"SingleChoice fields on {cell_type} but not on {row_type}",
    )

    if config.note_field is not None:
        note_field = config.note_field
    else:
        note_candidates = _candidates(cell_fields, row_fields, TYPE_STRING, set())
        if len(note_candidates) > 1:
            raise MatrixConfigError(
                f"parity matrix: cannot infer the note field: "
                f"{sorted(note_candidates)} are all String fields on "
                f"{cell_type} but not on {row_type}. Set "
                f'MatrixConfig(note_field="...") to pick one, or "" to '
                f"disable chip tooltips."
            )
        note_field = next(iter(note_candidates), "")

    columns = _options(row_fields, column_field)
    if not columns:
        raise MatrixConfigError(
            f"parity matrix: {row_type}.{column_field} declares no options, "
            f"so the matrix would have no columns."
        )

    declared = _options(cell_fields, status_field)
    if not declared:
        raise MatrixConfigError(
            f"parity matrix: {cell_type}.{status_field} declares no options, "
            f"so cells cannot be coloured."
        )

    mapping = dict(config.status_classes)
    unmapped = [status_ for status_ in declared if status_ not in mapping]
    if unmapped:
        raise MatrixConfigError(
            f"parity matrix: {cell_type}.{status_field} declares "
            f"{unmapped}, which MatrixConfig.status_classes does not map to a "
            f"CSS class. Known: {sorted(mapping)}. Add the missing status "
            f"(worst first) so cells are coloured correctly."
        )

    # Severity order comes from status_classes, not the grammar: declaration
    # order in a grammar carries no severity meaning.
    statuses = tuple(
        status_ for status_, _ in config.status_classes if status_ in declared
    )
    return _Resolved(
        row_node_type=row_type,
        cell_node_type=cell_type,
        column_field=column_field,
        status_field=status_field,
        note_field=note_field,
        columns=columns,
        statuses=statuses,
        status_class={status_: mapping[status_] for status_ in statuses},
        status_rank={status_: rank for rank, status_ in enumerate(statuses)},
        row_header=config.row_header,
        title=config.title,
        row_filter_parents=tuple(config.row_filter_parents),
        tab_order=tuple(config.tab_order),
        tab_label_prefix=config.tab_label_prefix,
        row_filter_all_label=config.row_filter_all_label,
        row_filter_label=config.row_filter_label,
    )


def _infer_axes(
    config: MatrixConfig, grammar: Dict[str, Dict[str, object]]
) -> Tuple[str, str, str]:
    """
    Find the field narrowed from MultipleChoice to SingleChoice.

    A parent declaring the set of columns it applies to, and a child naming the
    single column it belongs to, is exactly the shape this matrix renders, so
    that one narrowing identifies all three axes at once.
    """
    triples = []
    for row_type_, row_fields_ in grammar.items():
        for title_ in row_fields_:
            if _type_of(row_fields_, title_) != TYPE_MULTIPLE_CHOICE:
                continue
            for cell_type_, cell_fields_ in grammar.items():
                if cell_type_ == row_type_:
                    continue
                if _type_of(cell_fields_, title_) == TYPE_SINGLE_CHOICE:
                    triples.append((row_type_, cell_type_, title_))

    if config.row_node_type is not None:
        triples = [t for t in triples if t[0] == config.row_node_type]
    if config.cell_node_type is not None:
        triples = [t for t in triples if t[1] == config.cell_node_type]
    if config.column_field is not None:
        triples = [t for t in triples if t[2] == config.column_field]

    if len(triples) == 1:
        return triples[0]
    if not triples:
        raise MatrixConfigError(
            "parity matrix: no field is declared MultipleChoice on one node "
            "type and SingleChoice on another, so the matrix axes cannot be "
            "inferred. Set MatrixConfig(row_node_type=..., "
            "cell_node_type=..., column_field=...) explicitly. "
            f"Node types seen: {sorted(grammar)}."
        )
    raise MatrixConfigError(
        "parity matrix: the matrix axes are ambiguous. Candidates "
        f"(row, cell, field): {sorted(triples)}. Set the matching "
        "MatrixConfig fields to choose one."
    )


def _candidates(
    cell_fields: Dict[str, object],
    row_fields: Dict[str, object],
    gef_type: str,
    exclude: Set[str],
) -> Set[str]:
    """Fields of `gef_type` on the cell type that the row type does not have."""
    return {
        title_
        for title_ in cell_fields
        if title_ not in row_fields
        and title_ not in exclude
        and _type_of(cell_fields, title_) == gef_type
    }


def _infer_unique(
    candidates: Set[str], what: str, setting: str, detail: str
) -> str:
    if len(candidates) == 1:
        return next(iter(candidates))
    if not candidates:
        raise MatrixConfigError(
            f"parity matrix: cannot infer the {what}: no {detail}. "
            f'Set MatrixConfig({setting}="...") explicitly.'
        )
    raise MatrixConfigError(
        f"parity matrix: cannot infer the {what}: {sorted(candidates)} are "
        f'all {detail}. Set MatrixConfig({setting}="...") to pick one.'
    )


@dataclass
class Chip:
    """One cell-level requirement as it appears inside a cell."""

    uid: str
    status: str
    title: str
    note: str
    href: str


@dataclass
class Row:
    """One row-level requirement and its cells, keyed by column value."""

    uid: str
    title: str
    group: str
    scope: Set[str]
    cells: Dict[str, List[Chip]] = field(default_factory=dict)
    document: str = ""
    parents: Tuple[str, ...] = ()


def _node_href(node: SDocNode, link_renderer: LinkRenderer) -> str:
    """
    Link from this screen to a requirement node.

    Not LinkRenderer.render_node_link: with no context document it passes
    level 0 into DocumentMeta.get_root_path_prefix, where `if not
    other_doc_level` treats 0 as absent and falls back to the *target*
    document's own depth, prepending a spurious "../..". This screen sits at
    the output root, so the unprefixed document link is what we want.
    """
    document = node.get_parent_or_including_document()
    anchor = link_renderer.render_local_anchor(node)
    return f"{document.meta.get_html_doc_link()}#{anchor}"


def _field(node: SDocNode, title: str) -> str:
    if not title:
        return ""
    value = node.get_meta_field_value_by_title(title)
    return value.strip() if value is not None else ""


def _parent_uids(node: SDocNode) -> List[str]:
    """Every Parent relation. A child may legitimately refine several rows."""
    uids = []
    for relation_ in node.relations:
        if relation_.ref_type == PARENT_RELATION:
            ref_uid = getattr(relation_, "ref_uid", None)
            if ref_uid:
                uids.append(ref_uid)
    return uids


def _group_of(node: SDocNode) -> str:
    """Enclosing section title, or "" when the node sits outside a section.

    Grouping by the document's own sections rather than by a UID naming
    convention keeps this generic and gives readable labels.
    """
    parent = getattr(node, "parent", None)
    while parent is not None:
        if getattr(parent, "node_type", None) == SECTION_NODE_TYPE:
            return getattr(parent, "reserved_title", None) or ""
        parent = getattr(parent, "parent", None)
    return ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "group"


class ParityMatrixGenerator:
    """Subclass and override `config` to reuse this on another project."""

    config = MatrixConfig()

    @classmethod
    def export(
        cls,
        project_config: ProjectConfig,
        traceability_index: TraceabilityIndex,
        link_renderer: LinkRenderer,
        html_templates: HTMLTemplates,
    ) -> Markup:
        cfg = _resolve(cls.config, _grammar_fields(traceability_index))

        rows: "OrderedDict[str, Row]" = OrderedDict()
        cell_nodes: List[SDocNode] = []
        filter_titles: Dict[str, str] = {}

        for document_ in traceability_index.document_tree.document_list:
            iterator = SDocDocumentIterator(document_)
            for node_, _ in iterator.all_content(print_fragments=False):
                if not isinstance(node_, SDocNode):
                    continue
                uid = node_.reserved_uid
                if uid is None:
                    continue
                if uid in cfg.row_filter_parents:
                    filter_titles[uid] = node_.reserved_title or uid

                if node_.node_type == cfg.row_node_type:
                    scope = {
                        value_.strip()
                        for value_ in _field(node_, cfg.column_field).split(",")
                        if value_.strip()
                    }
                    rows[uid] = Row(
                        uid=uid,
                        title=node_.reserved_title or "",
                        group=_group_of(node_),
                        scope=scope,
                        document=getattr(document_, "title", "") or "",
                        parents=tuple(_parent_uids(node_)),
                        cells={column_: [] for column_ in cfg.columns},
                    )
                elif node_.node_type == cfg.cell_node_type:
                    cell_nodes.append(node_)

        for node_ in cell_nodes:
            column = _field(node_, cfg.column_field)
            if column not in cfg.columns:
                continue
            chip = Chip(
                uid=node_.reserved_uid or "",
                status=_field(node_, cfg.status_field),
                title=node_.reserved_title or "",
                note=_field(node_, cfg.note_field),
                href=_node_href(node_, link_renderer),
            )
            for parent_uid_ in _parent_uids(node_):
                row = rows.get(parent_uid_)
                if row is not None:
                    row.cells[column].append(chip)

        view_object = ProjectStatisticsViewObject(
            traceability_index=traceability_index,
            project_config=project_config,
            link_renderer=link_renderer,
            metrics=(
                _build_toggle_metrics(cfg, list(rows.values()), filter_titles)
                if cfg.row_filter_parents or cfg.tab_order
                else _build_all_metrics(cfg, list(rows.values()))
            ),
        )
        return view_object.render_screen(html_templates.jinja_environment())


def _worst(cfg: _Resolved, chips: List[Chip]) -> Optional[str]:
    if not chips:
        return None
    return min(
        chips, key=lambda chip_: cfg.status_rank.get(chip_.status, 0)
    ).status


def _class_of(cfg: _Resolved, status: Optional[str]) -> str:
    return cfg.status_class.get(status or "", UNKNOWN_STATUS_CLASS)


def _render_cell(cfg: _Resolved, row: Row, column: str) -> str:
    if column not in row.scope:
        return (
            '<td class="cell out" title="Out of scope: '
            f'{escape(row.uid)} does not apply to {escape(column)}">'
            '<span class="dash">&mdash;</span></td>'
        )

    chips = row.cells.get(column, [])
    if not chips:
        # A fan-out audit normally makes this unreachable; render it loudly
        # rather than silently as a blank if it ever happens.
        return (
            f'<td class="cell missing" title="No {escape(cfg.cell_node_type)}'
            ' for this cell">!</td>'
        )

    parts = []
    for chip_ in sorted(chips, key=lambda c: c.uid):
        tooltip = f"{chip_.uid} — {chip_.title} [{chip_.status}]"
        if chip_.note:
            tooltip += f"\n{cfg.note_field}: {chip_.note}"
        parts.append(
            f'<a class="chip {_class_of(cfg, chip_.status)}" '
            f'href="{escape(chip_.href)}" title="{escape(tooltip)}">'
            f"{escape(chip_.uid)}</a>"
        )
    # Joined with whitespace, not "": adjacent inline-block chips with no
    # whitespace between them give the line breaker no wrap opportunity, so a
    # multi-chip cell would be one unbreakable run and would overflow.
    return (
        f'<td class="cell {_class_of(cfg, _worst(cfg, chips))}">'
        + " ".join(parts)
        + "</td>"
    )


def _percentages(counts: Sequence[int]) -> List[int]:
    """Integer percentages summing to exactly 100 (largest remainder).

    The same integers drive both the segment widths and their labels, so the
    bar always fills the track and can never disagree with its own numbers.
    """
    total = sum(counts)
    if total <= 0:
        return [0] * len(counts)
    # Integer arithmetic on purpose: with floats, two mathematically equal
    # remainders can compare unequal, making the tie-break arbitrary.
    out = [count_ * 100 // total for count_ in counts]
    remainders = [count_ * 100 % total for count_ in counts]
    # Hand the leftover points to the largest remainders; ties go to the
    # earlier status, which is the better one.
    order = sorted(
        range(len(counts)), key=lambda i: (-remainders[i], i)
    )
    for index_ in order[: 100 - sum(out)]:
        out[index_] += 1
    return out


def _render_bar(
    segments: Sequence[Tuple[str, str, int]],
) -> str:
    """Stacked proportional bar from (label, css class, count) triples.

    Every segment counts the same thing -- one cell of this column -- so the
    percentages share a denominator and the columns are comparable.
    """
    percents = _percentages([count_ for _, _, count_ in segments])

    parts = []
    for (label_, css_class_, count_), percent_ in zip(segments, percents):
        if percent_ <= 0:
            continue
        tooltip = f"{count_} {label_} ({percent_}%)"
        # Below this width a label clips into noise; the tooltip still has it.
        text = f"{percent_}%" if percent_ >= _BAR_LABEL_MIN_PERCENT else ""
        parts.append(
            f'<span class="parity-bar-seg {css_class_}" '
            f'style="width:{percent_}%" title="{escape(tooltip)}">'
            f"{text}</span>"
        )

    return (
        '<span class="parity-bar">'
        '<span class="parity-bar-track">' + "".join(parts) + "</span></span>"
    )


def _build_all_metrics(
    cfg: _Resolved, rows: List[Row]
) -> List[Union[Metric, MetricSection]]:
    """One self-contained summary and matrix per row document.

    A project that keeps its rows in several documents (e.g. separate
    requirement sets) gets one matrix per document, in document order; a
    single document renders exactly as before.
    """
    by_document: "OrderedDict[str, List[Row]]" = OrderedDict()
    for row_ in rows:
        by_document.setdefault(row_.document, []).append(row_)
    if len(by_document) <= 1:
        return _build_metrics(cfg, rows)
    metrics: List[Union[Metric, MetricSection]] = []
    for document_title, document_rows in by_document.items():
        metrics.extend(_build_metrics(cfg, document_rows, heading=document_title))
    return metrics


def _named(heading: str, name: str) -> str:
    return f"{heading} — {name}" if heading else name


def _build_metrics(
    cfg: _Resolved, rows: List[Row], heading: str = ""
) -> List[Union[Metric, MetricSection]]:
    """
    Summary rows use the key-value component as intended; only the grid is
    custom HTML.
    """
    # Tally cells, not nodes, taking each cell's worst status -- the same
    # roll-up the grid paints. Every column then totals the row count, so
    # out-of-scope sits on the same bar instead of being a separate figure
    # with a denominator of its own.
    tally: Dict[str, Dict[str, int]] = {
        column_: {status_: 0 for status_ in cfg.statuses}
        for column_ in cfg.columns
    }
    out_of_scope_by_column: Dict[str, int] = {
        column_: 0 for column_ in cfg.columns
    }
    for row_ in rows:
        for column_ in cfg.columns:
            if column_ not in row_.scope:
                out_of_scope_by_column[column_] += 1
                continue
            worst = _worst(cfg, row_.cells.get(column_, []))
            if worst in tally[column_]:
                tally[column_][worst] += 1

    in_scope = sum(len(row_.scope) for row_ in rows)
    total_cells = len(rows) * len(cfg.columns)
    metrics: List[Union[Metric, MetricSection]] = []

    scope_section = MetricSection(name=_named(heading, "Scope"), metrics=[])
    metrics.append(scope_section)
    scope_section.metrics.append(
        Metric(
            name="Cells in scope",
            value=Markup(
                f"{in_scope} of {total_cells} "
                f"({len(rows)} rows &times; {len(cfg.columns)} "
                f"{escape(cfg.column_field)} values)"
            ),
        )
    )
    scope_section.metrics.append(
        Metric(
            name="Deliberately out of scope",
            value=Markup(
                f"{total_cells - in_scope} &mdash; a value omitted from a "
                f"row's {escape(cfg.column_field)} set is not a gap"
            ),
        )
    )

    per_column = MetricSection(
        name=_named(heading, f"Per {cfg.column_field.lower()}"), metrics=[]
    )
    metrics.append(per_column)
    for column_ in cfg.columns:
        segments = [
            (status_, _class_of(cfg, status_), tally[column_][status_])
            for status_ in reversed(cfg.statuses)
        ]
        segments.append(
            (
                "Out of scope",
                OUT_OF_SCOPE_CLASS,
                out_of_scope_by_column[column_],
            )
        )
        per_column.metrics.append(
            Metric(name=column_, value=Markup(_render_bar(segments)))
        )

    matrix_section = MetricSection(name=_named(heading, cfg.title), metrics=[])
    metrics.append(matrix_section)
    matrix_section.metrics.append(
        Metric(
            name=f"{cfg.row_header} × {cfg.column_field.lower()}",
            value=Markup(_render_matrix(cfg, rows, id_prefix=_slug(heading) if heading else "")),
        )
    )
    return metrics


def _render_matrix(
    cfg: _Resolved,
    rows: List[Row],
    id_prefix: str = "",
    columns: Optional[Sequence[str]] = None,
    row_classes: Optional[Dict[str, str]] = None,
    column_classes: Optional[Dict[str, str]] = None,
    legend: bool = True,
) -> str:
    columns = tuple(columns) if columns is not None else cfg.columns
    row_classes = row_classes or {}
    column_classes = column_classes or {}
    parts = ['<div class="parity">']

    if legend:
        parts.append(_legend_html(cfg))

    parts.append(
        '<table class="parity-table"><thead><tr>'
        f"<th>{escape(cfg.row_header)}</th>"
    )
    for column_ in columns:
        column_class_ = column_classes.get(column_, "")
        class_attr = f' class="{column_class_}"' if column_class_ else ""
        parts.append(f"<th{class_attr}>{escape(column_)}</th>")
    parts.append("</tr></thead>")

    # One <tbody> per group. A sticky table cell is constrained by its section,
    # so this is what makes each group label pin only while its own group is on
    # screen and get pushed out by the next one. A single tbody would pin them
    # all at the same offset, stacked.
    for group_, group_rows in groupby(rows, key=lambda r: r.group):
        group_rows = list(group_rows)
        # Tag the section with the row-filter options its rows match (row
        # class "rp-0" -> section class "tp-0"), so the filter can hide a
        # section it empties.
        needed = sorted(
            {
                token[3:]
                for row_ in group_rows
                for token in row_classes.get(row_.uid, "").split()
                if token.startswith("rp-")
            }
        )
        tbody_attr = f' class="{" ".join("tp-" + i for i in needed)}"' if needed else ""
        # One cell per column rather than a colspan: hiding a column must drop
        # its cell from every row, or the table keeps an empty column slot.
        filler = "".join(
            f'<td class="{column_classes[c]}"></td>' if column_classes.get(c) else "<td></td>"
            for c in columns
        )
        parts.append(
            f'<tbody{tbody_attr}><tr class="area" '
            f'id="group-{id_prefix + "-" if id_prefix else ""}{_slug(group_)}">'
            f"<td>{escape(group_)}</td>{filler}</tr>"
        )
        for row_ in group_rows:
            row_class_ = row_classes.get(row_.uid, "")
            tr_attr = f' class="{row_class_}"' if row_class_ else ""
            parts.append(
                f'<tr{tr_attr}><th class="req"><span class="uid">'
                f"{escape(row_.uid)}</span>"
                f'<span class="rt">{escape(row_.title)}</span></th>'
            )
            for column_ in columns:
                cell = _render_cell(cfg, row_, column_)
                if column_classes.get(column_):
                    cell = cell.replace('<td class="', f'<td class="{column_classes[column_]} ', 1)
                parts.append(cell)
            parts.append("</tr>")
        parts.append("</tbody>")
    parts.append("</table></div>")
    return "".join(parts)


def _legend_html(cfg: _Resolved) -> str:
    """Static legend: one chip per status, then out of scope."""
    parts = ['<div class="parity-legend">']
    for status_ in reversed(cfg.statuses):
        parts.append(
            f'<span class="chip {_class_of(cfg, status_)}">'
            f"{escape(status_)}</span>"
        )
    parts.append(f'<span class="chip {OUT_OF_SCOPE_CLASS}">Out of scope</span>')
    hint = "Cell background shows the worst status it contains."
    if cfg.note_field:
        hint += f" Hover a chip for its {cfg.note_field}."
    parts.append(f'<span class="parity-hint">{escape(hint)}</span></div>')
    return "".join(parts)


def _filter_chip(input_id: str, css_class: str, label: str) -> str:
    """A legend chip that is a checkbox, ticked by default."""
    return (
        f'<label class="{css_class} parity-filter">'
        f'<input type="checkbox" id="{input_id}" checked>{escape(label)}</label>'
    )


def _row_filter_selector(cfg: _Resolved, titles: Dict[str, str]) -> str:
    """Breadcrumb-style radio pills: one per filter parent, then "all"."""
    options = [
        (f"pf-{index_}", titles.get(uid_, uid_), uid_)
        for index_, uid_ in enumerate(cfg.row_filter_parents)
    ]
    options.append(("pf-all", cfg.row_filter_all_label, ""))
    parts = ['<span class="parity-selector">']
    for input_id, label_, uid_ in options:
        checked = " checked" if input_id == "pf-all" else ""
        title_attr = f' title="{escape(uid_)}"' if uid_ else ""
        parts.append(
            f'<input type="radio" name="parity-row-filter" id="{input_id}"{checked}>'
            f'<label for="{input_id}"{title_attr}>{escape(label_)}</label>'
        )
    parts.append("</span>")
    return "".join(parts)


def _column_segments(
    cfg: _Resolved, rows: List[Row], column_: str
) -> List[Tuple[str, str, int]]:
    """Bar segments for one column: each status (worst per cell), then out of scope."""
    counts = {status_: 0 for status_ in cfg.statuses}
    out_of_scope = 0
    for row_ in rows:
        if column_ not in row_.scope:
            out_of_scope += 1
            continue
        worst = _worst(cfg, row_.cells.get(column_, []))
        if worst in counts:
            counts[worst] += 1
    segments = [
        (status_, _class_of(cfg, status_), counts[status_])
        for status_ in reversed(cfg.statuses)
    ]
    segments.append(("Out of scope", OUT_OF_SCOPE_CLASS, out_of_scope))
    return segments


def _build_toggle_metrics(
    cfg: _Resolved, rows: List[Row], filter_titles: Dict[str, str]
) -> List[Union[Metric, MetricSection]]:
    """Overall bars, then one tab per row document under a filtering legend.

    CSS-only filters, each a generated :has() rule:
    - a legend chip (checkbox) hides rows containing none of the highlighted
      statuses;
    - the row filter (radios after the tabs) keeps only the rows refining the
      chosen parent, and hides the sections it empties.
    """
    missing = [uid_ for uid_ in cfg.row_filter_parents if uid_ not in filter_titles]
    if missing:
        raise MatrixConfigError(
            f"parity matrix: row_filter_parents {missing} are not nodes of this project."
        )

    row_classes = {}
    status_keys = set()
    for row_ in rows:
        classes = [
            f"rp-{index_}"
            for index_, uid_ in enumerate(cfg.row_filter_parents)
            if uid_ in row_.parents
        ]
        statuses = set()
        for column_ in cfg.columns:
            if column_ not in row_.scope:
                statuses.add(OUT_OF_SCOPE_CLASS)
            else:
                statuses |= {_class_of(cfg, c.status) for c in row_.cells.get(column_, [])}
        if statuses:
            classes.append("ss-" + "-".join(sorted(statuses)))
            status_keys.add(classes[-1])
        row_classes[row_.uid] = " ".join(classes)

    hide = [
        ".parity-view"
        + "".join(f":has(#fs-{s}:not(:checked))" for s in key[3:].split("-"))
        + f" tr.{key}"
        for key in sorted(status_keys)
    ]
    for index_ in range(len(cfg.row_filter_parents)):
        chosen = f".parity-view:has(#pf-{index_}:checked)"
        hide.append(f"{chosen} tbody tr:not(.area):not(.rp-{index_})")
        hide.append(f"{chosen} tbody:not(.tp-{index_})")
    style = f"{', '.join(hide)} {{ display: none; }}" if hide else ""

    # Filter box: one labelled group per filter, shown under the tabs.
    status_chips = [
        _filter_chip(f"fs-{_class_of(cfg, s)}", f"chip {_class_of(cfg, s)}", s)
        for s in reversed(cfg.statuses)
    ]
    status_chips.append(
        _filter_chip(f"fs-{OUT_OF_SCOPE_CLASS}", f"chip {OUT_OF_SCOPE_CLASS}", "Out of scope")
    )
    groups = [(cfg.status_field.title(), f'<div class="parity-legend">{"".join(status_chips)}</div>')]
    if cfg.row_filter_parents:
        groups.append((cfg.row_filter_label, _row_filter_selector(cfg, filter_titles)))
    hint = "Click to filter. Cell background shows the worst status it contains."
    if cfg.note_field:
        hint += f" Hover a requirement for its {cfg.note_field}."
    filters_html = (
        '<section class="parity-filters">'
        + "".join(
            f'<div class="parity-filter-group"><span class="parity-filter-label">'
            f"{escape(label_)}</span>{body_}</div>"
            for label_, body_ in groups
        )
        + f'<p class="parity-hint">{escape(hint)}</p></section>'
    )

    by_document: "OrderedDict[str, List[Row]]" = OrderedDict()
    for row_ in rows:
        by_document.setdefault(row_.document, []).append(row_)
    order = [t for t in cfg.tab_order if t in by_document]
    order += [t for t in by_document if t not in order]
    prefix = cfg.tab_label_prefix

    def label_of(title_: str) -> str:
        return title_[len(prefix):] if prefix and title_.startswith(prefix) else title_

    parts = ['<div class="parity parity-view">']
    if style:
        parts.append(f"<style>{style}</style>")
    matrices = [
        (
            label_of(title_),
            _render_matrix(
                cfg,
                by_document[title_],
                id_prefix=_slug(title_),
                row_classes=row_classes,
                legend=False,
            ),
        )
        for title_ in order
    ]
    if len(matrices) > 1:
        # An "All" tab first: every row, grouped by document then section.
        all_rows = [
            replace(row_, group=f"{label_of(title_)} › {row_.group}")
            for title_ in order
            for row_ in by_document[title_]
        ]
        matrices.insert(
            0,
            (
                "All",
                _render_matrix(
                    cfg, all_rows, id_prefix="all", row_classes=row_classes, legend=False
                ),
            ),
        )
        # CSS-only tabs: the nth checked radio shows the nth panel.
        parts.append('<div class="parity-tabs">')
        for index_, (label_, _) in enumerate(matrices):
            checked = " checked" if index_ == 0 else ""
            parts.append(
                f'<input type="radio" name="parity-doc" id="pd-{index_}"{checked}>'
                f'<label for="pd-{index_}">{escape(label_)}</label>'
            )
        # A <section>, not a <div>, so the panels' nth-of-type count holds.
        parts.append(filters_html)
        for _, matrix_ in matrices:
            parts.append(f'<div class="parity-panel">{matrix_}</div>')
        parts.append("</div>")
    else:
        parts.append(filters_html)
        parts.extend(matrix_ for _, matrix_ in matrices)
    parts.append("</div>")

    in_scope = sum(len(row_.scope) for row_ in rows)
    total_cells = len(rows) * len(cfg.columns)
    scope_section = MetricSection(
        name="Scope",
        metrics=[
            Metric(
                name="Cells in scope",
                value=Markup(
                    f"{in_scope} of {total_cells} "
                    f"({len(rows)} rows &times; {len(cfg.columns)} "
                    f"{escape(cfg.column_field)} values)"
                ),
            ),
            Metric(
                name="Deliberately out of scope",
                value=Markup(
                    f"{total_cells - in_scope} &mdash; a value omitted from a "
                    f"row's {escape(cfg.column_field)} set is not a gap"
                ),
            ),
        ],
    )
    per_column = MetricSection(
        name=f"Per {cfg.column_field.lower()}",
        metrics=[
            Metric(
                name=column_,
                value=Markup(_render_bar(_column_segments(cfg, rows, column_))),
            )
            for column_ in cfg.columns
        ],
    )
    view_section = MetricSection(
        name=cfg.title,
        metrics=[Metric(name="Requirements", value=Markup("".join(parts)))],
    )
    return [scope_section, per_column, view_section]
