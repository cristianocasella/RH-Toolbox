#!/usr/bin/env python3
"""MCP server for Red Hat Offline Knowledge Portal (RHOKP).

Exposes RHOKP's Solr search (full-text, semantic, hybrid) and static
KB content (solutions, articles, CVEs, product lifecycles) as MCP tools.

Usage:
    python3 rhokp_mcp_server.py
"""

import asyncio
import json
import os
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional

import httpx  # pylint: disable=import-error
from mcp import types  # pylint: disable=import-error
from mcp.server import Server  # pylint: disable=import-error
from mcp.server.stdio import stdio_server  # pylint: disable=import-error

RHOKP_BASE = os.environ.get("RHOKP_BASE_URL", "http://localhost:8080")

TOOLS = [
    types.Tool(
        name="search",
        description=(
            "Full-text keyword search across Red Hat knowledgebase solutions, "
            "articles, documentation, and errata. Best for exact terms, error "
            "messages, solution IDs, or CVE numbers."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query (keywords, error text, solution ID, etc.)"
                    ),
                },
                "rows": {
                    "type": "integer",
                    "description": ("Number of results to return (default 5, max 20)"),
                    "default": 5,
                },
                "document_kind": {
                    "type": "string",
                    "description": (
                        "Filter by type: solution, article, " "documentation, errata"
                    ),
                    "enum": [
                        "solution",
                        "article",
                        "documentation",
                        "errata",
                    ],
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="semantic_search",
        description=(
            "Semantic (vector) search over the RAG chunk database (~1.2M "
            "chunks). Best for natural language questions like "
            "'how do I resize a PV in OpenShift'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": ("Natural language question or topic description"),
                },
                "rows": {
                    "type": "integer",
                    "description": ("Number of results to return (default 5, max 20)"),
                    "default": 5,
                },
                "product": {
                    "type": "string",
                    "description": (
                        "Filter by product (e.g. "
                        "'openshift_container_platform', "
                        "'red_hat_enterprise_linux')"
                    ),
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="hybrid_search",
        description=(
            "Combined keyword + semantic search over the RAG database. "
            "Recommended default — gives the best of both full-text "
            "precision and semantic understanding."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query — works with both keywords "
                        "and natural language"
                    ),
                },
                "rows": {
                    "type": "integer",
                    "description": ("Number of results to return (default 5, max 20)"),
                    "default": 5,
                },
                "product": {
                    "type": "string",
                    "description": (
                        "Filter by product (e.g. "
                        "'openshift_container_platform', "
                        "'red_hat_enterprise_linux')"
                    ),
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="get_solution",
        description=(
            "Retrieve a specific Red Hat knowledgebase solution by its "
            "numeric ID. Returns the full solution text including "
            "environment, issue, and resolution."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "solution_id": {
                    "type": "string",
                    "description": "Numeric solution ID (e.g. '7079626')",
                },
            },
            "required": ["solution_id"],
        },
    ),
    types.Tool(
        name="get_article",
        description=(
            "Retrieve a specific Red Hat knowledgebase article by its " "numeric ID."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "article_id": {
                    "type": "string",
                    "description": "Numeric article ID (e.g. '7134680')",
                },
            },
            "required": ["article_id"],
        },
    ),
    types.Tool(
        name="get_product_lifecycle",
        description=(
            "Get lifecycle dates for a Red Hat product. Returns support "
            "start/end dates, versions, and support status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "product_name": {
                    "type": "string",
                    "description": (
                        "Product name (e.g. 'Red Hat Enterprise Linux', "
                        "'OpenShift Container Platform 4')"
                    ),
                },
            },
            "required": ["product_name"],
        },
    ),
]


class HTMLTextExtractor(HTMLParser):
    """Extracts visible text from HTML, focusing on main content areas."""

    def __init__(self):
        super().__init__()
        self._in_main = False
        self._skip = 0
        self._skip_tags = {"script", "style", "nav", "footer", "header"}
        self.lines: List[str] = []

    def handle_starttag(self, tag, attrs):
        """Track entry into main content and skippable regions."""
        attrs_dict = dict(attrs)
        if tag == "main" or attrs_dict.get("id") == "defined-article-body":
            self._in_main = True
        if tag in self._skip_tags:
            self._skip += 1

    def handle_endtag(self, tag):
        """Track exit from skippable regions."""
        if tag in self._skip_tags:
            self._skip -= 1

    def handle_data(self, data):
        """Collect non-empty text inside main content."""
        if self._skip > 0:
            return
        stripped = data.strip()
        if stripped and self._in_main:
            self.lines.append(stripped)


def extract_text(html_content: str) -> str:
    """Extract visible text from HTML content."""
    extractor = HTMLTextExtractor()
    extractor.feed(html_content)
    return "\n".join(extractor.lines)


async def do_solr_search(
    core: str,
    endpoint: str,
    query: str,
    rows: int,
    extra_params: Optional[Dict] = None,
) -> List[Dict]:
    """Execute a Solr search query and return formatted results."""
    rows = min(max(rows, 1), 20)
    params: Dict = {"q": query, "rows": str(rows), "wt": "json"}
    if extra_params:
        params.update(extra_params)

    url = f"{RHOKP_BASE}/solr/{core}/{endpoint}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    docs = data.get("response", {}).get("docs", [])
    num_found = data.get("response", {}).get("numFound", 0)

    results = []
    for doc in docs:
        result = {
            "title": doc.get("title", ""),
            "score": doc.get("score"),
            "url": doc.get("id") or doc.get("resourceName", ""),
        }
        if doc.get("documentKind"):
            result["type"] = doc["documentKind"]
        if doc.get("product"):
            result["product"] = doc["product"]
        if doc.get("headings"):
            result["headings"] = doc["headings"]
        if doc.get("product_version"):
            result["version"] = doc["product_version"]
        if doc.get("online_source_url"):
            result["online_url"] = doc["online_source_url"]

        content = doc.get("chunk") or doc.get("main_content", "")
        if content:
            result["content"] = content[:2000]

        results.append(result)

    return [{"total_results": num_found, "returned": len(results)}] + results


async def do_get_page(path: str) -> str:
    """Fetch an HTML page from RHOKP and extract its text content."""
    url = f"{RHOKP_BASE}/{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return extract_text(resp.text)


async def do_get_lifecycle(product_name: str) -> str:
    """Query the RHOKP product lifecycle API."""
    url = f"{RHOKP_BASE}/product-life-cycles/api/v1/products/"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params={"name": product_name})
        if resp.status_code == 200:
            try:
                return json.dumps(resp.json(), indent=2)
            except ValueError:
                pass
        resp2 = await client.get(url)
        resp2.raise_for_status()
        try:
            data = resp2.json()
        except ValueError:
            return resp2.text[:5000]
        return json.dumps(data, indent=2)[:10000]


async def handle_list_tools(_ctx, _params):  # pylint: disable=unused-argument
    """Return the list of available MCP tools."""
    return types.ListToolsResult(tools=TOOLS)


async def handle_call_tool(
    _ctx, params: types.CallToolRequestParams
):  # pylint: disable=unused-argument
    """Dispatch an MCP tool call to the appropriate handler."""
    name = params.name
    args = params.arguments or {}

    try:
        if name == "search":
            extra = {}
            if args.get("document_kind"):
                extra["fq"] = f"documentKind:{args['document_kind']}"
            results = await do_solr_search(
                "portal",
                "select",
                args["query"],
                args.get("rows", 5),
                extra,
            )
            text = json.dumps(results, indent=2)

        elif name == "semantic_search":
            extra = {"df": "chunk"}
            if args.get("product"):
                extra["fq"] = f"product:{args['product']}"
            results = await do_solr_search(
                "portal-rag",
                "semantic-search",
                args["query"],
                args.get("rows", 5),
                extra,
            )
            text = json.dumps(results, indent=2)

        elif name == "hybrid_search":
            extra = {}
            if args.get("product"):
                extra["fq"] = f"product:{args['product']}"
            results = await do_solr_search(
                "portal-rag",
                "hybrid-search",
                args["query"],
                args.get("rows", 5),
                extra,
            )
            text = json.dumps(results, indent=2)

        elif name == "get_solution":
            sid = re.sub(r"[^0-9]", "", args["solution_id"])
            text = await do_get_page(f"solutions/{sid}/index.html")
            if not text:
                text = f"Solution {sid} not found in this RHOKP instance."

        elif name == "get_article":
            aid = re.sub(r"[^0-9]", "", args["article_id"])
            text = await do_get_page(f"articles/{aid}/index.html")
            if not text:
                text = f"Article {aid} not found in this RHOKP instance."

        elif name == "get_product_lifecycle":
            text = await do_get_lifecycle(args["product_name"])

        else:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

    except httpx.HTTPStatusError as exc:
        text = f"HTTP error {exc.response.status_code}: " f"{exc.response.text[:500]}"
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=True,
        )
    except httpx.ConnectError:
        text = (
            f"Cannot connect to RHOKP at {RHOKP_BASE}. "
            "Ensure the RHOKP container is running."
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=True,
        )

    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


server = Server(
    "rhokp",
    version="0.1.0",
    instructions=(
        "Red Hat Offline Knowledge Portal (RHOKP) MCP server. "
        "Provides offline access to Red Hat knowledgebase solutions, "
        "articles, documentation, CVEs, and product lifecycle data via "
        "Solr search. Use 'hybrid_search' as the default search tool — "
        "it combines keyword and semantic search for best results. Use "
        "'search' for exact IDs or error messages. Use 'get_solution' or "
        "'get_article' when you have a specific document ID."
    ),
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


async def main():
    """Start the MCP server over stdio."""
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
