#!/usr/bin/env python3
"""Audit the Qubes management-tooling requirements spec.

Checks what strictdoc does not: anchor IDs that resolve to real definitions,
fan-out between PRODUCT and actual L3 children, EARS conformance, and the
writing-style limits from spec/CONVENTIONS.md.

Run from the repo root:

    uv run python tools/audit_spec.py [--quiet]

Exit code 0 when clean, 1 when any violation is found.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "spec"
GRAMMAR = SPEC / "requirements.sgra"

AUTHORITY_PATH = "qubes-core-admin/qubes/api/admin.py"

# Each L2 requirement set lives in its own document with its own UID prefix.
L2_PREFIX_BY_FILE = {
    "L2_Provisioning.sdoc": "L2P-",
    "L2_Configuration.sdoc": "L2C-",
    "L2_Common.sdoc": "L2S-",
}


def grammar_choices(element_tag: str, field_title: str) -> Set[str]:
    """Option list of a SingleChoice/MultipleChoice field in the shared grammar.

    Element-aware on purpose: PRODUCT is declared on both PRODUCT_REQUIREMENT
    (MultipleChoice) and TECHNICAL_REQUIREMENT (SingleChoice), and the two must
    agree -- see Audit.check_grammar.
    """
    if not GRAMMAR.is_file():
        raise SystemExit(f"audit: shared grammar not found: {GRAMMAR}")

    text = GRAMMAR.read_text(encoding="utf-8")
    block = re.search(
        rf"^- TAG: {re.escape(element_tag)}$(.*?)(?=^- TAG: |\Z)",
        text,
        re.S | re.M,
    )
    if block is None:
        raise SystemExit(f"audit: grammar has no element '{element_tag}'")

    field_ = re.search(
        rf"^  - TITLE: {re.escape(field_title)}$\n    TYPE: \w*Choice\((.*?)\)$",
        block.group(1),
        re.M,
    )
    if field_ is None:
        raise SystemExit(
            f"audit: {element_tag} has no choice field '{field_title}' "
            f"in {GRAMMAR.name}"
        )

    return {opt.strip() for opt in field_.group(1).split(",") if opt.strip()}


def grammar_field_order(element_tag: str) -> List[str]:
    """Field titles of a grammar element, in the order StrictDoc requires."""
    text = GRAMMAR.read_text(encoding="utf-8")
    block = re.search(
        rf"^- TAG: {re.escape(element_tag)}$(.*?)(?=^- TAG: |\Z)",
        text,
        re.S | re.M,
    )
    if block is None:
        raise SystemExit(f"audit: grammar has no element '{element_tag}'")
    return re.findall(r"^  - TITLE: (\S+)$", block.group(1), re.M)


# Single source of truth: everything below is read out of requirements.sgra.
PRODUCTS = grammar_choices("TECHNICAL_REQUIREMENT", "PRODUCT")
STATUSES = grammar_choices("TECHNICAL_REQUIREMENT", "STATUS")
EARS_KINDS = grammar_choices("TECHNICAL_REQUIREMENT", "EARS_PATTERN")

# Product name -> the code used in L3 UIDs (L3-<CODE>-<NNN>).
PRODUCT_CODES = {
    "Ansible": "ANS",
    "Salt": "SLS",
    "QubesadminTools": "QTL",
    "Terraform": "TF",
}

STATUS_UNIMPLEMENTED = "Not Implemented"
STATUS_IMPLEMENTED = "Implemented"
STATUS_PARTIAL = "Partial"
# Not implemented by the product, but achievable with the host tool's own
# features; its Implementation anchor points at code showing the workaround.
STATUS_WORKAROUND = "Workaround"

MAX_STATEMENT_WORDS = 25
MAX_TITLE_WORDS = 6
MAX_RATIONALE_SENTENCES = 2

FILLER = [
    "in order to",
    "be able to",
    "it should be noted",
    "as appropriate",
    "etc.",
    "and/or",
    "successfully",
]

# Kept in code, not in the grammar: these encode the grammar of English that
# each EARS pattern demands, not project configuration. check_grammar asserts
# every key still exists in the .sgra, so adding a pattern there without a rule
# here fails loudly.
EARS_PREFIX = {
    "Event-driven": re.compile(r"^When\b", re.I),
    "State-driven": re.compile(r"^While\b", re.I),
    "Optional-feature": re.compile(r"^Where\b", re.I),
    "Unwanted-behaviour": re.compile(r"^If\b", re.I),
}


@dataclass
class Relation:
    type: str
    role: Optional[str] = None
    value: Optional[str] = None
    path: Optional[str] = None
    element: Optional[str] = None
    id: Optional[str] = None
    line_range: Optional[str] = None


@dataclass
class Node:
    tag: str
    doc: str
    fields: Dict[str, str] = field(default_factory=dict)
    relations: List[Relation] = field(default_factory=list)

    @property
    def uid(self) -> str:
        return self.fields.get("UID", "<no-uid>")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

# Matches both plain nodes "[REQUIREMENT]" and composite section markers
# "[[SECTION]]" / "[[/SECTION]]". The composite forms must terminate a node's
# field block, otherwise a section's TITLE: is absorbed into the preceding
# requirement and its own TITLE check silently never runs.
NODE_RE = re.compile(r"^\[\[?/?([A-Z][A-Z_]*)\]\]?$")


def parse_sdoc(path: Path) -> List[Node]:
    """Minimal .sdoc reader: enough for auditing, not a general parser."""
    nodes: List[Node] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    in_grammar = False

    while i < len(lines):
        line = lines[i]

        if line.strip() == "[GRAMMAR]":
            in_grammar = True
            i += 1
            continue

        m = NODE_RE.match(line.strip())
        if m and m.group(1) not in ("DOCUMENT", "GRAMMAR"):
            in_grammar = False
            node = Node(tag=m.group(1), doc=path.name)
            i += 1
            while i < len(lines) and not NODE_RE.match(lines[i].strip()):
                cur = lines[i]

                if cur.strip() == "RELATIONS:":
                    i += 1
                    while i < len(lines) and lines[i].startswith("- TYPE: "):
                        rel = Relation(type=lines[i][len("- TYPE: ") :].strip())
                        i += 1
                        while i < len(lines) and lines[i].startswith("  ") and ": " in lines[i]:
                            k, _, v = lines[i].strip().partition(": ")
                            setattr_map = {
                                "ROLE": "role",
                                "VALUE": "value",
                                "PATH": "path",
                                "ELEMENT": "element",
                                "ID": "id",
                                "LINE_RANGE": "line_range",
                            }
                            if k in setattr_map:
                                setattr(rel, setattr_map[k], v.strip())
                            i += 1
                        node.relations.append(rel)
                    continue

                fm = re.match(r"^([A-Z][A-Z_]*): ?(.*)$", cur)
                if fm:
                    key, val = fm.group(1), fm.group(2)
                    if val.strip() == ">>>":
                        i += 1
                        buf = []
                        while i < len(lines) and lines[i].strip() != "<<<":
                            buf.append(lines[i])
                            i += 1
                        node.fields[key] = " ".join(
                            x.strip() for x in buf
                        ).strip()
                    else:
                        node.fields[key] = val.strip()
                i += 1

            if not in_grammar:
                nodes.append(node)
            continue

        i += 1

    return nodes


def collect() -> Tuple[List[Node], List[Node], List[Node]]:
    l1 = [n for n in parse_sdoc(SPEC / "L1_Goals.sdoc") if n.tag == "SYSTEM_GOAL"]
    l2 = [
        n
        for path in sorted(SPEC.glob("L2_*.sdoc"))
        for n in parse_sdoc(path)
        if n.tag == "PRODUCT_REQUIREMENT"
    ]
    l3 = [
        n
        for n in parse_sdoc(SPEC / "L3_Technical.sdoc")
        if n.tag == "TECHNICAL_REQUIREMENT"
    ]
    return l1, l2, l3


# --------------------------------------------------------------------------
# Anchor resolution
# --------------------------------------------------------------------------

_qualname_cache: Dict[Path, Set[str]] = {}


def qualified_names(path: Path) -> Set[str]:
    """Every dotted def/class name in a Python file, as strictdoc names them."""
    if path in _qualname_cache:
        return _qualname_cache[path]

    names: Set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        _qualname_cache[path] = names
        return names

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                dotted = f"{prefix}{child.name}"
                names.add(dotted)
                walk(child, dotted + ".")

    walk(tree, "")
    _qualname_cache[path] = names
    return names


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


class Audit:
    def __init__(self) -> None:
        self.problems: List[Tuple[str, str]] = []

    def fail(self, category: str, msg: str) -> None:
        self.problems.append((category, msg))

    # -- grammar --------------------------------------------------------
    def check_grammar(self) -> None:
        """The shared grammar must be internally consistent.

        PRODUCT is declared twice -- MultipleChoice on PRODUCT_REQUIREMENT
        (which products a requirement applies to) and SingleChoice on
        TECHNICAL_REQUIREMENT (which product it describes). The fan-out rule is
        meaningless unless the two option lists are identical, and nothing in
        strictdoc enforces that.
        """
        l1_products = grammar_choices("SYSTEM_GOAL", "PRODUCT")
        if l1_products != PRODUCTS:
            self.fail(
                "grammar",
                "PRODUCT option lists have drifted: "
                f"SYSTEM_GOAL {sorted(l1_products)} vs TECHNICAL_REQUIREMENT {sorted(PRODUCTS)}",
            )
        l2_products = grammar_choices("PRODUCT_REQUIREMENT", "PRODUCT")
        if l2_products != PRODUCTS:
            only_l2 = sorted(l2_products - PRODUCTS)
            only_l3 = sorted(PRODUCTS - l2_products)
            self.fail(
                "grammar",
                "PRODUCT option lists have drifted: "
                f"only on PRODUCT_REQUIREMENT {only_l2}, "
                f"only on TECHNICAL_REQUIREMENT {only_l3}",
            )

        unknown_patterns = sorted(set(EARS_PREFIX) - EARS_KINDS)
        if unknown_patterns:
            self.fail(
                "grammar",
                f"EARS_PREFIX names patterns absent from the grammar: {unknown_patterns}",
            )

    # -- structure ------------------------------------------------------
    def check_structure(self, l1: List[Node], l2: List[Node], l3: List[Node]) -> None:
        l1_uids = {n.uid for n in l1}
        l2_uids = {n.uid for n in l2}

        if not l2:
            self.fail("structure", "no L2 requirements found in spec/L2_*.sdoc")
        for n in l2:
            prefix = L2_PREFIX_BY_FILE.get(n.doc)
            if prefix is None:
                self.fail("structure", f"{n.uid}: {n.doc} is not a known L2 document")
            elif not n.uid.startswith(prefix):
                self.fail("structure", f"{n.uid}: UID in {n.doc} must start with {prefix}")

        # Three-digit numbers (see CONVENTIONS "Numbering"); a trailing 0 is
        # not enforced, so numbers inserted between existing ones stay valid.
        missing_codes = sorted(PRODUCTS - set(PRODUCT_CODES))
        if missing_codes:
            self.fail("grammar", f"no L3 UID code for product(s) {missing_codes}")
        l2_prefixes = "|".join(re.escape(p) for p in L2_PREFIX_BY_FILE.values())
        l2_form = re.compile(rf"(?:{l2_prefixes})[A-Z]+-\d{{3}}")
        l3_codes = "|".join(sorted(PRODUCT_CODES[p] for p in PRODUCTS if p in PRODUCT_CODES))
        l3_form = re.compile(rf"L3-(?:{l3_codes})-\d{{3}}")
        for n in l2:
            if not l2_form.fullmatch(n.uid):
                self.fail("structure", f"{n.uid}: L2 UID must be <prefix><AREA>-<NNN>")
        for n in l3:
            if not l3_form.fullmatch(n.uid):
                self.fail("structure", f"{n.uid}: L3 UID must be L3-<PRODUCT>-<NNN>")

        for n in l2:
            parents = [r for r in n.relations if r.type == "Parent"]
            if not parents:
                self.fail("structure", f"{n.uid}: no Parent to an L1")
            for r in parents:
                if r.value not in l1_uids:
                    self.fail("structure", f"{n.uid}: Parent {r.value} is not an L1")
                if r.role != "Refines":
                    self.fail("structure", f"{n.uid}: Parent to {r.value} lacks ROLE: Refines")

        for n in l3:
            parents = [r for r in n.relations if r.type == "Parent"]
            if not parents:
                self.fail("structure", f"{n.uid}: no Parent to an L2")
            for r in parents:
                if r.value not in l2_uids:
                    self.fail("structure", f"{n.uid}: Parent {r.value} is not an L2")

            status = n.fields.get("STATUS", "")
            impl = [r for r in n.relations if r.role == "Implementation"]
            if status == STATUS_UNIMPLEMENTED and impl:
                self.fail(
                    "structure",
                    f"{n.uid}: STATUS is '{STATUS_UNIMPLEMENTED}' but has "
                    f"{len(impl)} Implementation anchor(s)",
                )
            if status != STATUS_UNIMPLEMENTED and not impl:
                self.fail("structure", f"{n.uid}: no Implementation anchor")

        # L2s with no children at all
        parented: Set[str] = set()
        for n in l3:
            for r in n.relations:
                if r.type == "Parent" and r.value:
                    parented.add(r.value)
        for n in l2:
            if n.uid not in parented:
                self.fail("structure", f"{n.uid}: no L3 children")

    # -- fan-out --------------------------------------------------------
    def check_fanout(self, l2: List[Node], l3: List[Node]) -> None:
        applies: Dict[str, Set[str]] = {}
        for n in l2:
            raw = n.fields.get("PRODUCT", "")
            applies[n.uid] = {p.strip() for p in raw.split(",") if p.strip()}
            unknown = applies[n.uid] - PRODUCTS
            if unknown:
                self.fail("fan-out", f"{n.uid}: unknown PRODUCT value(s) {sorted(unknown)}")

        have: Dict[Tuple[str, str], bool] = {}
        for n in l3:
            comp = n.fields.get("PRODUCT", "")
            if comp not in PRODUCTS:
                self.fail("fan-out", f"{n.uid}: PRODUCT '{comp}' is not a known product")
            for r in n.relations:
                if r.type == "Parent" and r.value:
                    have[(r.value, comp)] = True
                    if r.value in applies and comp not in applies[r.value]:
                        self.fail(
                            "fan-out",
                            f"{n.uid}: PRODUCT {comp} is outside {r.value}'s PRODUCT set",
                        )

        for uid, comps in applies.items():
            for comp in sorted(comps):
                if not have.get((uid, comp)):
                    self.fail("fan-out", f"missing cell: ({uid}, {comp}) has no L3")

    # -- L1 -> L2 applicability -----------------------------------------
    def check_l1_fanout(self, l1: List[Node], l2: List[Node]) -> None:
        """PRODUCT narrows downwards, as it does from L2 to L3.

        An L2 may apply only to products that every L1 parent applies to, and
        every (goal x product) pair must be realised by at least one L2.
        """
        goal_products: Dict[str, Set[str]] = {}
        for g in l1:
            ps = {p.strip() for p in g.fields.get("PRODUCT", "").split(",") if p.strip()}
            if not ps:
                self.fail("fan-out", f"{g.uid}: no PRODUCT")
            unknown = ps - PRODUCTS
            if unknown:
                self.fail("fan-out", f"{g.uid}: unknown PRODUCT value(s) {sorted(unknown)}")
            goal_products[g.uid] = ps

        realised: Set[Tuple[str, str]] = set()
        for n in l2:
            ps = {p.strip() for p in n.fields.get("PRODUCT", "").split(",") if p.strip()}
            for r in n.relations:
                if r.type != "Parent" or r.value not in goal_products:
                    continue
                outside = ps - goal_products[r.value]
                if outside:
                    self.fail(
                        "fan-out",
                        f"{n.uid}: PRODUCT {sorted(outside)} is outside {r.value}'s PRODUCT set",
                    )
                realised |= {(r.value, p) for p in ps & goal_products[r.value]}

        for goal, ps in goal_products.items():
            for p in sorted(ps):
                if (goal, p) not in realised:
                    self.fail("fan-out", f"missing L2: ({goal}, {p}) has no L2 refining it")

    # -- anchors --------------------------------------------------------
    def check_anchors(self, nodes: List[Node]) -> None:
        for n in nodes:
            for r in n.relations:
                if r.type != "File":
                    continue
                if not r.role:
                    self.fail("anchor", f"{n.uid}: File relation without ROLE")
                if not r.path:
                    self.fail("anchor", f"{n.uid}: File relation without PATH")
                    continue

                if r.role == "Authority" and r.path != AUTHORITY_PATH:
                    self.fail(
                        "anchor",
                        f"{n.uid}: Authority points at {r.path}, only {AUTHORITY_PATH} is allowed",
                    )
                if r.role != "Authority" and r.path.startswith("qubes-core-admin/"):
                    self.fail(
                        "anchor",
                        f"{n.uid}: {r.role} anchor points into core-admin ({r.path})",
                    )

                full = REPO / r.path
                if not full.is_file():
                    self.fail("anchor", f"{n.uid}: PATH does not exist: {r.path}")
                    continue

                if r.line_range:
                    try:
                        begin, end = (int(x) for x in r.line_range.split(","))
                    except ValueError:
                        self.fail(
                            "anchor",
                            f"{n.uid}: LINE_RANGE '{r.line_range}' is not '<begin>, <end>'",
                        )
                        continue
                    total = len(
                        full.read_text(encoding="utf-8", errors="replace").splitlines()
                    )
                    if not 1 <= begin <= end <= total:
                        self.fail(
                            "anchor",
                            f"{n.uid}: LINE_RANGE {begin}, {end} is outside {r.path} "
                            f"(lines 1-{total})",
                        )

                if r.id:
                    if not r.path.endswith(".py"):
                        self.fail(
                            "anchor",
                            f"{n.uid}: {r.path} is not a .py file; use LINE_RANGE, not ELEMENT/ID",
                        )
                        continue
                    names = qualified_names(full)
                    if r.id not in names:
                        near = [x for x in names if x.rsplit(".", 1)[-1] == r.id.rsplit(".", 1)[-1]]
                        hint = f" (did you mean {near[0]}?)" if near else ""
                        self.fail(
                            "anchor",
                            f"{n.uid}: ID '{r.id}' not found in {r.path}{hint} "
                            f"— SILENT FAILURE, build stays green",
                        )

    # -- style ----------------------------------------------------------
    def check_style(self, nodes: List[Node], statement_limit: int) -> None:
        seen: Dict[Tuple[str, str], str] = {}
        for n in nodes:
            stmt = n.fields.get("STATEMENT", "")
            title = n.fields.get("TITLE", "")

            words = len(stmt.split())
            if words > statement_limit:
                self.fail("style", f"{n.uid}: STATEMENT is {words} words (limit {statement_limit})")

            body = stmt.rstrip()
            if body.count(". ") >= 1 or body.rstrip(".").count(".") >= 1:
                sentences = [s for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
                if len(sentences) > 1:
                    self.fail("style", f"{n.uid}: STATEMENT is {len(sentences)} sentences, must be one")

            if len(title.split()) > MAX_TITLE_WORDS:
                self.fail(
                    "style",
                    f"{n.uid}: TITLE is {len(title.split())} words (limit {MAX_TITLE_WORDS})",
                )

            low = stmt.lower()
            for f in FILLER:
                if f in low:
                    self.fail("style", f"{n.uid}: STATEMENT contains filler '{f}'")
            if low.count(" shall ") > 1:
                self.fail("style", f"{n.uid}: STATEMENT uses 'shall' more than once")

            pattern = n.fields.get("EARS_PATTERN", "")
            rx = EARS_PREFIX.get(pattern)
            if rx and not rx.match(stmt):
                self.fail(
                    "style",
                    f"{n.uid}: EARS_PATTERN is {pattern} but STATEMENT does not start accordingly",
                )
            if pattern == "Ubiquitous" and re.match(r"^(When|While|Where|If)\b", stmt, re.I):
                self.fail("style", f"{n.uid}: EARS_PATTERN is Ubiquitous but STATEMENT is conditional")

            rationale = n.fields.get("RATIONALE", "")
            if rationale:
                sentences = [s for s in re.split(r"(?<=[.!?])\s+", rationale.strip()) if s.strip()]
                if len(sentences) > MAX_RATIONALE_SENTENCES:
                    self.fail(
                        "style",
                        f"{n.uid}: RATIONALE is {len(sentences)} sentences (limit {MAX_RATIONALE_SENTENCES})",
                    )
                if rationale.strip().lower() == stmt.strip().lower():
                    self.fail("style", f"{n.uid}: RATIONALE restates STATEMENT")

            comp = n.fields.get("PRODUCT", "")
            key = (comp, re.sub(r"[^a-z ]", "", stmt.lower()))
            if key in seen and comp:
                self.fail("style", f"{n.uid}: statement duplicates {seen[key]} in the same component")
            elif comp:
                seen[key] = n.uid

    # -- fields ---------------------------------------------------------
    def check_fields(self, l3: List[Node]) -> None:
        for n in l3:
            status = n.fields.get("STATUS", "")
            defect = n.fields.get("DEFECT", "")
            if status in (STATUS_PARTIAL, STATUS_UNIMPLEMENTED) and not defect:
                self.fail("fields", f"{n.uid}: STATUS is '{status}' but DEFECT is empty")
            # STATUS_WORKAROUND: DEFECT is optional; when present it names the gap.
            if status == STATUS_IMPLEMENTED and defect:
                self.fail(
                    "fields",
                    f"{n.uid}: STATUS is '{STATUS_IMPLEMENTED}' but carries a DEFECT",
                )
            for required in ("PRODUCT", "STATUS", "EARS_PATTERN"):
                if not n.fields.get(required):
                    self.fail("fields", f"{n.uid}: missing {required}")


    # -- field order ----------------------------------------------------
    def check_field_order(self, nodes: List[Node], element_tag: str) -> None:
        """Fields must follow the grammar's order, or StrictDoc refuses the file.

        The export fails outright on a misordered node while this audit stayed
        green, so catch it here.
        """
        order = grammar_field_order(element_tag)
        for n in nodes:
            present = [f for f in n.fields if f in order]
            if present != sorted(present, key=order.index):
                self.fail(
                    "fields",
                    f"{n.uid}: fields out of grammar order {present}; "
                    f"grammar order is {[f for f in order if f in present]}",
                )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    ap.add_argument(
        "--fragment",
        metavar="FILE",
        help="audit anchors and style in a standalone L3 fragment "
        "(no document header needed) instead of the full spec",
    )
    args = ap.parse_args()

    if args.fragment:
        nodes = [
            n
            for n in parse_sdoc(Path(args.fragment))
            if n.tag == "TECHNICAL_REQUIREMENT"
        ]
        audit = Audit()
        audit.check_anchors(nodes)
        audit.check_style(nodes, MAX_STATEMENT_WORDS)
        audit.check_fields(nodes)
        audit.check_field_order(nodes, "TECHNICAL_REQUIREMENT")
        print(f"fragment {args.fragment}: {len(nodes)} requirement(s)")
        if not audit.problems:
            print("fragment audit: clean")
            return 0
        for cat, msg in audit.problems:
            print(f"  [{cat}] {msg}")
        print(f"\nfragment audit: {len(audit.problems)} problem(s)")
        return 1

    l1, l2, l3 = collect()
    audit = Audit()

    audit.check_grammar()
    audit.check_structure(l1, l2, l3)
    audit.check_fanout(l2, l3)
    audit.check_l1_fanout(l1, l2)
    audit.check_anchors(l2 + l3)
    audit.check_style(l2, MAX_STATEMENT_WORDS)
    audit.check_style(l3, MAX_STATEMENT_WORDS)
    audit.check_fields(l3)
    audit.check_field_order(l1, "SYSTEM_GOAL")
    audit.check_field_order(l2, "PRODUCT_REQUIREMENT")
    audit.check_field_order(l3, "TECHNICAL_REQUIREMENT")

    print(f"L1 goals: {len(l1)}   L2 requirements: {len(l2)}   L3 requirements: {len(l3)}")

    if not audit.problems:
        print("audit: clean")
        return 0

    by_cat: Dict[str, List[str]] = {}
    for cat, msg in audit.problems:
        by_cat.setdefault(cat, []).append(msg)

    if not args.quiet:
        for cat in ("grammar", "structure", "fan-out", "anchor", "style", "fields"):
            msgs = by_cat.get(cat)
            if not msgs:
                continue
            print(f"\n{cat.upper()} ({len(msgs)})")
            for m in msgs:
                print(f"  - {m}")

    print(f"\naudit: {len(audit.problems)} problem(s) across {len(by_cat)} categor(ies)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
