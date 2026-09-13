# Authoring conventions for this specification

Prose conventions for everyone writing `.sdoc` files here.

**The machine-readable definitions live in `spec/requirements.sgra`** — node
types, field sets, choice enumerations and relation roles. All documents
import it with `[GRAMMAR]` / `IMPORT_FROM_FILE: requirements.sgra`, and
`tools/audit_spec.py` reads its enumerations rather than hardcoding them. To add
a product, edit that one file; the documents and the audit follow automatically.
This file deliberately does not restate those values.

## Levels and UIDs

| Level | Document | Node tag | UID form |
|---|---|---|---|
| L1 system goals | `L1_Goals.sdoc` | `SYSTEM_GOAL` | `L1-<AREA>` |
| L2 provisioning | `L2_Provisioning.sdoc` | `PRODUCT_REQUIREMENT` | `L2P-<AREA>-<NN>` |
| L2 configuration management | `L2_Configuration.sdoc` | `PRODUCT_REQUIREMENT` | `L2C-<AREA>-<NN>` |
| L2 common | `L2_Common.sdoc` | `PRODUCT_REQUIREMENT` | `L2S-<AREA>-<NN>` |
| L3 technical requirements | `L3_Technical.sdoc` | `TECHNICAL_REQUIREMENT` | `L3-<ANS\|SLS\|QTL\|TF>-<NNN>` |

Sections use the composite form `[[SECTION]] … [[/SECTION]]`. Plain `[SECTION]`
is rejected by current strictdoc.

Requirements about the Qubes system as a whole rather than a single qube (global
properties, qrexec policy, qube listing) go in a **Qubes system** section of the
set they belong to, with the UID area `SYSTEM`. In the L3 document, each product
section is grouped into Provisioning / Configuration / Common, and each group
mirrors the area sections of its L2 document.

## Requirement sets

L2 is split by what a requirement is about: the infrastructure objects, or what
runs inside them.

- **Provisioning** (`L2_Provisioning.sdoc`): qubes and their dom0-side
  attributes (create, clone, remove, class, properties, features, tags, notes,
  volumes, pools, devices, netvm, firewall, templates, global properties, qrexec
  policy), plus runtime operations (power state, transient device attachment).
- **Configuration** (`L2_Configuration.sdoc`): what runs inside qubes. That is
  commands and files in qubes, the management DisposableVM mechanics, and
  delegation from a provisioning tool to a configuration tool.
- **Common** (`L2_Common.sdoc`): what every management tool is measured on,
  namely convergence, inspection, untrusted-data confinement and diagnostics.

Out of scope for now: recoverability (backup and restore) and dom0 software.

Every L2 that realises state in Provisioning or Configuration refines
`L1-MANAGE`, plus each quality goal it genuinely serves. Multiple parents are
expected at L2 as at L3; list the purpose goal first.

Products:
- **Terraform** is a provisioning tool. In Configuration it appears only on
  delegation.
- **Ansible and Salt** cover both sets, and configure a qube through a
  disposable management qube.
- **QubesadminTools** is mostly provisioning. The exception is `qvm-run`, which
  runs commands inside qubes but has no desired-state convergence.

## Products

The `PRODUCT` field names the management products in scope. It is declared twice
in the grammar, and the two declarations differ only in multiplicity:

- **L2** — `MultipleChoice`. Every product the requirement applies to. This is
  the input to the fan-out rule below.
- **L3** — `SingleChoice`. The one product this requirement describes.

The option lists must be identical; `audit_spec.py` fails with a `grammar`
problem if they drift.

Products map to trees as follows: **Ansible** → `qubes-ansible/`; **Salt** →
`qubes-mgmt-salt-dom0-qvm/` *and* `qubes-mgmt-salt/` (one product, two repos);
**QubesadminTools** → `qubes-core-admin-client/qubesadmin/tools/`; **Terraform** →
`qubes-terraform/`, whose `qubes_vm` resource runs a vendored copy of qubes-ansible
under `qubes_provider/utils/qubes_ansible/` (anchor that copy, not `qubes-ansible/`).

`qubes-core-admin` is **not** a product. It is the authority layer, reached only
through `ROLE: Authority`, and only `qubes/api/admin.py` is indexed.

## EARS

Every `STATEMENT` uses exactly one pattern, declared in `EARS_PATTERN`:

| Pattern | Template |
|---|---|
| Ubiquitous | `The <subject> shall <response>.` |
| Event-driven | `When <trigger>, the <subject> shall <response>.` |
| State-driven | `While <state>, the <subject> shall <response>.` |
| Optional-feature | `Where <feature>, the <subject> shall <response>.` |
| Unwanted-behaviour | `If <condition>, then the <subject> shall <response>.` |
| Complex | combination of the above |

Subjects, used verbatim: `the qube module`, `the qubes_proxy strategy`,
`the qvm execution module`, `the qvm state module`, `qubesctl`, `qvm-prefs`,
`the qubes_vm resource`, `the qubes_prefs resource`, `the qubes_policy resource`,
`the provider`, and similar concrete names. At L2 use `the management tool`.

## Writing style — less is more

Enforced mechanically by `tools/audit_spec.py`, not left to taste.

- **`STATEMENT`: one sentence, ≤ 25 words, exactly one verifiable behaviour.**
  If you need "and" to join two behaviours, split it — or the granularity is
  wrong.
- **No implementation narration.** The `Implementation` anchor carries the how.
  Write "shall reject a netvm that does not provide network", not "shall call
  `validate_properties`, which iterates the property dict".
- **`TITLE`: noun phrase, ≤ 6 words.** "Netvm validation", not a sentence.
- **`RATIONALE`: omit by default.** Only where the requirement would otherwise
  look arbitrary — a security reason, a Qubes constraint, a non-obvious
  ordering. Never a restatement. Two sentences maximum.
- **`DEFECT`: one sentence plus `file:line`.** The divergence, not its history.
- **Never restate the parent.** An L3 under `L2S-CONV-01` does not re-explain
  idempotency.
- **Banned filler:** "in order to", "be able to", "it should be noted that",
  "as appropriate", "etc.", "and/or", "successfully". One "shall" per statement.

Fewer, sharper requirements beat more, vaguer ones. Two L3s in the same
component that differ only in wording are one requirement.

## Relations

`ROLE:` is **mandatory on every relation** — grammar matching is on the exact
(type, role) pair, so a bare `- TYPE: Parent` is a build error.

```
RELATIONS:
- TYPE: Parent
  VALUE: L2P-CONFIG-01
  ROLE: Refines
- TYPE: File
  ROLE: Implementation
  PATH: qubes-ansible/ansible_collections/qubesos/core/plugins/module_utils/qubes_module_qube.py
  ELEMENT: function
  ID: QubeModule.enforce_properties
- TYPE: File
  ROLE: Verification
  PATH: qubes-ansible/tests/qubes/test_module_qube.py
  ELEMENT: function
  ID: test_change_properties_should_occur_only_when_necessary
```

- Paths are repo-root-relative POSIX.
- `ELEMENT: function` / `ELEMENT: class`, with `ID:` the **dotted Python name**
  (`Class.method` for methods, bare name at module level).
- For non-Python files (`qubesctl`, `qubes.SaltLinuxVM`, `qubes.AnsibleVM`,
  `.sls`) use `LINE_RANGE: <begin>, <end>` instead of `ELEMENT`/`ID`.
- `ROLE: Authority` may point only at `qubes-core-admin/qubes/api/admin.py`.

**Multiple parents are expected.** One `Parent` per L2 the requirement genuinely
refines; first listed is primary. Do not duplicate a behaviour to keep a single
parent.

## The silent-anchor hazard — read this before writing any anchor

An `ID:` naming a function that does not exist produces **no marker, no warning,
and a green build**. The requirement still registers against the file, so it
still *looks* covered. Only a bad *file path* raises an error.

This was demonstrated during setup: an anchor to `test_pause_and_resume` (the
real name is `test_lifecycle_pause_and_resume`) built cleanly and traced
nothing.

Therefore: **open the file and read the `def` line before writing an `ID:`.**
Never take a name from a summary, a memory, or an abbreviation. Then run:

```bash
uv run python tools/audit_spec.py
```

## Fan-out rule

Each L2 declares `PRODUCT`, the products it is in scope for. For **every**
(L2 × product in that set) pair there must be an L3 carrying that `PRODUCT` —
including where the product does not implement the behaviour, which gets
`STATUS: Not Implemented` and a `DEFECT` explaining the gap, and carries no
`Implementation` anchor.

Absence is invisible in a traceability matrix: a missing row cannot be
distinguished from an oversight, whereas a `Not Implemented` node is a finding.

Genuinely out-of-scope combinations are omitted from the L2's `PRODUCT` set — no
node, no false gap.

## Workaround status

`STATUS: Workaround` means the product does not implement the requirement itself,
but it can be met with the host tool's own features. Terraform's `local-exec`
running `qvm-run` is the canonical case.

- The `Implementation` anchor points at code showing the workaround, such as an
  example in the product's `examples/` directory, not at product code.
- `DEFECT` is optional. When present, it names the gap the workaround covers.
