"""Model Context Protocol (MCP) Client module.

Defines client components for registering and calling MCP tools,
along with parameters verification and refactoring fallback helpers.
"""

import re
import logging
from typing import Callable, Optional, Any

logger = logging.getLogger("self_governance.mcp")


class MCPClient:
    """A local client mapping for registering and invoking MCP tools."""

    def __init__(self) -> None:
        """Initializes the MCPClient."""
        self.schemas: dict[str, dict[str, Any]] = {}
        self.tool_implementations: dict[str, Callable[..., Any]] = {}

    def register_tool(self, name: str, schema: dict, implementation: Callable) -> None:
        """Registers a tool implementation along with its parameters schema.

        Args:
            name: String identifier of the tool.
            schema: Dictionary representing the JSON schema configuration.
            implementation: Callable tool handler.
        """
        self.schemas[name] = schema
        self.tool_implementations[name] = implementation
        logger.info("Registered MCP tool: %s", name)

    def call_tool(self, tool_name: str, args: dict) -> dict:
        """Invokes a registered tool, verifying arguments against its schema.

        Args:
            tool_name: String identifier of the tool.
            args: Dictionary of arguments to pass to the tool.

        Returns:
            A dictionary containing either {"status": "success", "result": Any}
            or {"status": "error", "error": str}.
        """
        if tool_name not in self.schemas:
            return {"status": "error", "error": f"Tool {tool_name} not found"}

        schema = self.schemas[tool_name]
        required = schema.get("required", [])
        for req in required:
            if req not in args:
                return {
                    "status": "error",
                    "error": f"Missing required parameter: '{req}'"
                }

        properties = schema.get("properties", {})
        for k, v in args.items():
            if k not in properties:
                return {
                    "status": "error",
                    "error": f"Invalid parameter: '{k}' not defined in schema."
                }
            expected_type = properties[k].get("type")
            type_mapping: dict[str, Any] = {
                "string": str,
                "integer": int,
                "number": (int, float),
                "boolean": bool,
                "array": list,
                "object": dict
            }
            if expected_type in type_mapping:
                if not isinstance(v, type_mapping[expected_type]):
                    return {
                        "status": "error",
                        "error": f"Type mismatch for '{k}': expected {expected_type}, got {type(v).__name__}."
                    }

        try:
            impl = self.tool_implementations[tool_name]
            result = impl(**args)
            return {"status": "success", "result": result}
        except Exception as e:
            return {"status": "error", "error": str(e)}


def refactor_and_retry_tool(error_msg: str, tool_name: str, args: dict, docs: str, client: Optional[MCPClient] = None) -> dict:
    """Attempts to dynamically refactor args after a validation failure and retries.

    Parses the error message to handle missing parameters, type mismatches,
    or param name renames based on heuristics and documentation context.

    Args:
        error_msg: The exception or validation failure message.
        tool_name: Name of the target tool.
        args: Dictionary of original arguments.
        docs: Documentation string to parse for details.
        client: Optional active MCPClient instance to perform the retry call.

    Returns:
        A dictionary containing the execution outcome status.
    """
    refactored = dict(args)
    logger.warning("Refactoring tool call '%s' due to error: %s", tool_name, error_msg)

    # 1. Handle missing parameter
    if "Missing required parameter" in error_msg:
        match = re.search(r"['\"]([^'\"]+)['\"]", error_msg)
        if match:
            missing_param = match.group(1)
            prop_type = "string"
            if client and tool_name in client.schemas:
                schema = client.schemas[tool_name]
                prop_type = schema.get("properties", {}).get(missing_param, {}).get("type", "string")
            else:
                pattern = rf"{missing_param}\s*\(([^)]+)\)"
                type_match = re.search(pattern, docs, re.IGNORECASE)
                if type_match:
                    prop_type = type_match.group(1).strip().lower()
                elif "integer" in docs or "int" in docs:
                    prop_type = "integer"

            if prop_type in ("integer", "int", "number"):
                refactored[missing_param] = 0
            elif prop_type in ("boolean", "bool"):
                refactored[missing_param] = False
            else:
                refactored[missing_param] = "default_val"

    # 2. Handle type mismatch
    if "Type mismatch" in error_msg:
        match = re.search(r"['\"]([^'\"]+)['\"]", error_msg)
        if match:
            mismatched_param = match.group(1)
            val = refactored.get(mismatched_param)
            if "integer" in error_msg or "int" in error_msg:
                try:
                    if val is None:
                        refactored[mismatched_param] = 0
                    else:
                        refactored[mismatched_param] = int(float(val))
                except (ValueError, TypeError):
                    refactored[mismatched_param] = 0
            elif "string" in error_msg or "str" in error_msg:
                refactored[mismatched_param] = str(val)
            elif "boolean" in error_msg or "bool" in error_msg:
                if str(val).lower() in ("true", "1", "yes"):
                    refactored[mismatched_param] = True
                else:
                    refactored[mismatched_param] = False

    # 3. Handle invalid parameter rename
    if "Invalid parameter" in error_msg:
        match = re.search(r"['\"]([^'\"]+)['\"]", error_msg)
        if match:
            invalid_param = match.group(1)
            # Find the closest match in the documentation
            for word in docs.split():
                clean_word = word.strip(":,.'\"()[]{}")
                if clean_word and clean_word != invalid_param:
                    if clean_word in invalid_param or invalid_param in clean_word:
                        if invalid_param in refactored:
                            refactored[clean_word] = refactored.pop(invalid_param)
                        break

    if client:
        return client.call_tool(tool_name, refactored)

    return {"status": "refactored", "args": refactored}

