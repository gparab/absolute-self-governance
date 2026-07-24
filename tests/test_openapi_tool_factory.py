"""Tests for register_tools_from_openapi_schema -- auto-generating MCP tools
from an OpenAPI spec instead of hand-writing each schema/implementation."""

import json
from unittest.mock import patch

import pytest

from self_governance.mcp import MCPClient, register_tools_from_openapi_schema


_PETSTORE_SCHEMA = {
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/pets/{petId}": {
            "get": {
                "operationId": "getPet",
                "summary": "Fetch a pet by ID",
                "parameters": [
                    {"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "verbose", "in": "query", "required": False, "schema": {"type": "boolean"}},
                ],
            }
        },
        "/pets": {
            "post": {
                "operationId": "createPet",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "properties": {
                                    "name": {"type": "string"},
                                    "age": {"type": "integer"},
                                },
                                "required": ["name"],
                            }
                        }
                    }
                },
            }
        },
    },
}


def test_registers_one_tool_per_operation():
    client = MCPClient()
    registered = register_tools_from_openapi_schema(client, _PETSTORE_SCHEMA)
    assert set(registered) == {"getPet", "createPet"}
    assert "getPet" in client.schemas
    assert "createPet" in client.schemas


def test_path_and_query_params_captured_in_schema():
    client = MCPClient()
    register_tools_from_openapi_schema(client, _PETSTORE_SCHEMA)
    schema = client.schemas["getPet"]
    assert schema["properties"]["petId"]["type"] == "integer"
    assert schema["properties"]["verbose"]["type"] == "boolean"
    assert schema["required"] == ["petId"]


def test_request_body_properties_captured_in_schema():
    client = MCPClient()
    register_tools_from_openapi_schema(client, _PETSTORE_SCHEMA)
    schema = client.schemas["createPet"]
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["age"]["type"] == "integer"
    assert schema["required"] == ["name"]


def test_missing_required_param_rejected_by_call_tool():
    client = MCPClient()
    register_tools_from_openapi_schema(client, _PETSTORE_SCHEMA)
    result = client.call_tool("getPet", {})
    assert result["status"] == "error"


def test_generated_implementation_calls_correct_url_and_method(monkeypatch):
    client = MCPClient()
    register_tools_from_openapi_schema(client, _PETSTORE_SCHEMA)

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"id": 5, "name": "Rex"}).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return FakeResponse()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.call_tool("getPet", {"petId": 5, "verbose": True})

    assert result["status"] == "success"
    assert result["result"] == {"id": 5, "name": "Rex"}
    assert captured["url"] == "https://api.example.com/pets/5?verbose=True"
    assert captured["method"] == "GET"


def test_generated_implementation_sends_json_body_for_post(monkeypatch):
    client = MCPClient()
    register_tools_from_openapi_schema(client, _PETSTORE_SCHEMA)

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"status": "created"}).encode()

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        captured["url"] = req.full_url
        return FakeResponse()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.call_tool("createPet", {"name": "Rex", "age": 3})

    assert result["status"] == "success"
    assert captured["body"] == {"name": "Rex", "age": 3}
    assert captured["url"] == "https://api.example.com/pets"


def test_raises_without_base_url_or_servers():
    client = MCPClient()
    schema_no_servers = {"paths": _PETSTORE_SCHEMA["paths"]}
    with pytest.raises(ValueError):
        register_tools_from_openapi_schema(client, schema_no_servers)


def test_explicit_base_url_overrides_servers():
    client = MCPClient()
    register_tools_from_openapi_schema(client, _PETSTORE_SCHEMA, base_url="https://override.example.com")

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return FakeResponse()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.call_tool("getPet", {"petId": 1})

    assert captured["url"].startswith("https://override.example.com")


def test_operation_without_operation_id_gets_sanitized_fallback_name():
    client = MCPClient()
    schema = {
        "servers": [{"url": "https://api.example.com"}],
        "paths": {"/foo/bar": {"delete": {}}},
    }
    registered = register_tools_from_openapi_schema(client, schema)
    assert registered == ["delete__foo_bar"]
