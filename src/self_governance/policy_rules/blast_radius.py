"""Differential-reachability blast-radius check (OpenLore's pattern, July
2026 topic-page batch): given a module that's about to change, find every
other module in the provided set that depends on it, directly or
transitively, so a diff's blast radius is visible before it lands. Reuses
the same import-parsing infra as the pre-edit boundary gate
(import_boundary.py)."""

from typing import Dict, List, Set

from self_governance.policy_rules.import_boundary import extract_imports


def compute_blast_radius(changed_module: str, modules: Dict[str, str]) -> List[str]:
    """modules: a mapping of module name -> source code for the modules to
    consider (e.g. every self_governance.* module in the package). Returns
    the sorted list of module names that import changed_module, directly
    or through a chain of other modules in the set.
    """
    direct_importers: Dict[str, Set[str]] = {name: set() for name in modules}
    for name, source in modules.items():
        for imported in extract_imports(source):
            for candidate in modules:
                if candidate != name and (
                    imported == candidate or imported.startswith(candidate + ".")
                ):
                    direct_importers[candidate].add(name)

    visited: Set[str] = set()
    frontier = list(direct_importers.get(changed_module, set()))
    while frontier:
        node = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        frontier.extend(direct_importers.get(node, set()) - visited)
    return sorted(visited)
