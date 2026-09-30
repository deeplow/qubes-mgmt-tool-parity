# SPDX-FileCopyrightText: 2026 deeplow
# SPDX-License-Identifier: Apache-2.0

"""Project configuration of the parity matrix (see tools/parity_matrix.py).

Wired in via `statistics_generator` in strictdoc_config.py. The generator stays
project-agnostic; everything specific to this spec lives here.
"""

from tools.parity_matrix import MatrixConfig, ParityMatrixGenerator


class QubesParityMatrixGenerator(ParityMatrixGenerator):
    """Adds the purpose-goal row filter and ordered tabs to the statistics screen."""

    config = MatrixConfig(
        # L1 goals also declare a MultipleChoice PRODUCT, so the row type is
        # no longer unambiguous from the grammar alone.
        row_node_type="PRODUCT_REQUIREMENT",
        # The three purpose goals; choosing one keeps only the requirements
        # that refine it. Labels come from the goals' titles.
        row_filter_parents=("L1-STATE", "L1-RECONCILE", "L1-OPERATE"),
        row_filter_label="Goal",
        tab_order=("L2 — Provisioning", "L2 — Configuration", "L2 — Common"),
        tab_label_prefix="L2 — ",
    )
