"""Complexity evaluation module.

Calculates the AST complexity of a project or list of Python target files
to determine if TETD consensus is warranted.
"""

import os
import ast
import logging
from typing import List, Optional

logger = logging.getLogger("self_governance.complexity")

def calculate_ast_complexity(working_directory: str, targets: Optional[List[str]] = None) -> int:
    """Calculates the total AST node count for target Python files.

    If targets is not provided, scans all .py files in the working directory
    (ignoring hidden files and common virtualenv directories).

    Args:
        working_directory: The base path to the project.
        targets: Optional list of specific file paths to evaluate.

    Returns:
        The total number of AST nodes found. Returns 0 if none found or on error.
    """
    total_nodes = 0
    files_to_parse = []

    if targets:
        for t in targets:
            full_path = os.path.join(working_directory, t)
            if os.path.isfile(full_path) and full_path.endswith('.py'):
                files_to_parse.append(full_path)
    else:
        ignore_dirs = {'.git', '.venv', 'venv', 'env', '__pycache__', 'node_modules', '.tox', '.planning'}
        for root, dirs, files in os.walk(working_directory):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.')]
            for file in files:
                if file.endswith('.py'):
                    files_to_parse.append(os.path.join(root, file))

    for file_path in files_to_parse:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            tree = ast.parse(content, filename=file_path)
            node_count = sum(1 for _ in ast.walk(tree))
            total_nodes += node_count
        except Exception as e:
            logger.warning(f"Could not parse {file_path} for complexity: {e}")

    logger.debug(f"Calculated total AST complexity: {total_nodes} nodes across {len(files_to_parse)} files.")
    return total_nodes
