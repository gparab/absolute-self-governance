"""Pre-edit import/architecture boundary gate (OpenLore's pattern, July 2026
topic-page batch): declares which layers may import which, and denies a
generated file's write before it lands on disk if its imports cross a
declared boundary. Opt-in -- callers with no LayerRules configured get no
opinion from this rule."""

import ast
import fnmatch
from dataclasses import dataclass, field
from typing import List, Optional

from self_governance.policy import Decision, PolicyAction, PolicyDecision


@dataclass
class LayerRule:
    """One architectural layer: the path globs that belong to it, and the
    import prefixes files in that layer are forbidden from using."""

    layer: str
    path_patterns: List[str]
    forbidden_imports: List[str] = field(default_factory=list)


def extract_imports(source: str) -> List[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    modules: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


class ImportBoundaryRule:
    """Denies a file write whose imports cross a declared architecture
    boundary. Evaluated against PolicyAction.content, so it only has an
    opinion on write-type actions that carry the new file's source."""

    name = "import_boundary"
    priority = 15

    def __init__(self, layer_rules: List[LayerRule]):
        self.layer_rules = layer_rules

    def evaluate(self, action: PolicyAction) -> Optional[PolicyDecision]:
        if not action.path or action.content is None:
            return None
        layer = next(
            (
                rule
                for rule in self.layer_rules
                if any(fnmatch.fnmatch(action.path, pat) for pat in rule.path_patterns)
            ),
            None,
        )
        if layer is None or not layer.forbidden_imports:
            return None
        for module in extract_imports(action.content):
            if any(
                module == forbidden or module.startswith(forbidden + ".")
                for forbidden in layer.forbidden_imports
            ):
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule_name=self.name,
                    reason=(
                        f"file '{action.path}' (layer '{layer.layer}') imports "
                        f"forbidden module '{module}'"
                    ),
                )
        return None
