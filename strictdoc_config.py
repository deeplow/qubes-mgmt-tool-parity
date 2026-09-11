from strictdoc.api import ProjectConfig


def create_config() -> ProjectConfig:
    return ProjectConfig(
        project_title="Qubes OS Management Tooling Requirements",
        source_root_path=".",
        statistics_generator="tools.parity_matrix.ParityMatrixGenerator",
        custom_css_path="tools/parity_matrix.css",
        project_features=[
            "TABLE_SCREEN",
            "TRACEABILITY_SCREEN",
            "DEEP_TRACEABILITY_SCREEN",
            "SEARCH",
            "PROJECT_STATISTICS_SCREEN",
            "TRACEABILITY_MATRIX_SCREEN",
            "REQUIREMENT_TO_SOURCE_TRACEABILITY",
            "TREE_MAP_SCREEN",
        ],
        include_doc_paths=["spec/**"],
        exclude_doc_paths=[".venv/**", "output/**"],
        include_source_paths=[
            # ANS — qubes-ansible
            "qubes-ansible/ansible_collections/**",
            "qubes-ansible/plugins/**",
            "qubes-ansible/qubes-rpc/**",
            "qubes-ansible/tests/**",
            "qubes-ansible/update-ansible-default-strategy",
            # SLS — one component, two repos.
            "qubes-mgmt-salt-dom0-qvm/_modules/**",
            "qubes-mgmt-salt-dom0-qvm/_states/**",
            "qubes-mgmt-salt-dom0-qvm/tests/**",
            "qubes-mgmt-salt/qubessalt/**",
            "qubes-mgmt-salt/qubesctl",
            "qubes-mgmt-salt/qubes.SaltLinuxVM",
            "qubes-mgmt-salt/srv/**",
            # QTL — the qvm-* CLI tools only. The rest of the qubesadmin/
            # library (app.py, backup/, vm/, ...) is deliberately not indexed;
            # widening this is a decision.
            "qubes-core-admin-client/qubesadmin/tools/**",
            # TF — qubes-terraform. Includes the vendored qubes-ansible copy
            # under qubes_provider/utils/qubes_ansible/, which the provider runs.
            "qubes-terraform/qubes_provider/**",
            "qubes-terraform/tests/**",
            # Examples back Workaround L3s (e.g. local-exec running qvm-run).
            "qubes-terraform/examples/**",
            # Authority layer: the Admin API surface ONLY. One file.
            # Not qubes/vm/, not qubes/storage/, not device or firewall
            # internals, not api/internal.py or api/misc.py. Widening this is
            # a decision.
            "qubes-core-admin/qubes/api/admin.py",
        ],
        exclude_source_paths=[
            "**/.git/**",
            ".venv/**",
            "**/__pycache__/**",
        ],
    )
