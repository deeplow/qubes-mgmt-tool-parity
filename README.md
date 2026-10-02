# Qubes OS Management Tooling Requirements

> **LLM DISCLAIMER**
>
> This was generated with LLMs, and wasn't exhaustively reviewed.
> Please use at your own risk.

A [StrictDoc](https://strictdoc.readthedocs.io/) specification of what Qubes OS
management tooling is required to do, and how far each tool actually implements
it.

The rendered specification is published at
<https://deeplow.github.io/qubes-mgmt-tool-parity/>, rebuilt on every push to
`main` and weekly.

[![Feature-parity matrix: product requirements down the side, Ansible, Salt,
QubesTools and Terraform across the top, coloured by implementation
status](https://raw.githubusercontent.com/deeplow/qubes-mgmt-tool-parity/main/docs/_assets/parity-matrix.png)](https://deeplow.github.io/qubes-mgmt-tool-parity/project_statistics.html)

| Path | What |
|---|---|
| `docs/spec/` | The specification: L1 goals, the three L2 requirement sets (`L2_Provisioning`, `L2_Configuration`, `L2_Common`), L3 technical requirements, the shared grammar, and authoring conventions |
| `tools/audit_spec.py` | Consistency audit of the specification |
| `tools/parity_matrix.py`, `.css` | Renders the feature-parity matrix screen |
| `strictdoc_config.py` | StrictDoc project configuration |
| `qubes-*/` | Upstream sources, as git submodules. Read-only here; requirement anchors point into them |
| `qubes-tools/` | The submodules behind the QubesTools product (qubes-core-admin-client, qubes-manager) |

## Running

Requires Python, [uv](https://docs.astral.sh/uv/) and git.

**Fetch the upstream sources first.** Requirement anchors resolve into the
submodules, and the export fails on a path that does not exist:

```bash
git submodule update --init
```

Build the HTML:

```bash
uv run strictdoc export docs --config .   # → output/html/index.html
```

Or serve it, which adds the search screen:

```bash
uv run strictdoc server docs --config .   # → http://127.0.0.1:5111
```

The **Statistics** screen in the navigation is the feature-parity matrix:
product requirements down the side, products across the top, coloured by
implementation status, with per-product progress bars above it.

## Checking the specification

```bash
uv run python tools/audit_spec.py  # expect: audit: clean
```

Run this before committing. It checks things StrictDoc does not — most
importantly that every anchor's `ID:` actually exists in the file it names. A
wrong `ID:` produces no marker, no warning and a green build, so the audit is
the only thing standing between a typo and a requirement that silently traces to
nothing.

```bash
uv run python tools/source_coverage.py --prefix qubes-terraform/
```

Lists the functions no requirement traces to, using StrictDoc's own coverage
index. A report, not a gate: use it to find missing anchors or requirements.

## Further reading

- `docs/spec/CONVENTIONS.md` — the conventions for this specification: levels and
  UIDs, the fan-out rule, EARS patterns, anchor style.
- [StrictDoc documentation](https://strictdoc.readthedocs.io/) — the tool
  itself, including how to write `.sdoc` documents.

## License

This repository is licensed under the Apache License, Version 2.0;
see [LICENSE](LICENSE).

The git submodules are separate projects and are **not** covered by this
license. Each one is distributed under its own license, found in its own
repository.
