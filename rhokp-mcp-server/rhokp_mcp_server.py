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
RHOKP_CONTAINER_NAME = os.environ.get("RHOKP_CONTAINER_NAME", "rhokp")
RHOKP_CONTAINER_TAG = os.environ.get("RHOKP_CONTAINER_TAG", "1784655565")
RHOKP_CONTAINER_IMAGE = os.environ.get(
    "RHOKP_CONTAINER_IMAGE",
    f"registry.redhat.io/offline-knowledge-portal/rhokp-rhel9:{RHOKP_CONTAINER_TAG}",
)
RHOKP_PODMAN_PATH = os.environ.get("RHOKP_PODMAN_PATH", "podman")
RHOKP_CONTAINER_PORT = os.environ.get("RHOKP_CONTAINER_PORT", "8080")
RHOKP_READY_TIMEOUT = int(os.environ.get("RHOKP_READY_TIMEOUT", "120"))
RHOKP_READY_INTERVAL = int(os.environ.get("RHOKP_READY_INTERVAL", "3"))
RHOKP_PIDS_LIMIT = os.environ.get("RHOKP_PIDS_LIMIT", "8192")
RHOKP_KEY_FILE = os.environ.get(
    "RHOKP_KEY_FILE",
    os.path.expanduser("~/.rhokp_key.txt"),
)

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
    types.Tool(
        name="container_status",
        description=(
            "Check the status of the RHOKP container. Returns whether "
            "it exists, its current state (running/stopped/etc.), and "
            "port mappings."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    types.Tool(
        name="container_start",
        description=(
            "Start the RHOKP container. If the container exists but is "
            "stopped, it is started. If it does not exist, a new container "
            "is created from the configured image and started."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    types.Tool(
        name="container_stop",
        description=("Stop the running RHOKP container gracefully."),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    types.Tool(
        name="container_restart",
        description=("Restart the RHOKP container. Equivalent to stop + start."),
        inputSchema={
            "type": "object",
            "properties": {},
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


async def _match_products(products: List[Dict], query: str) -> List[Dict]:
    """Filter products by exact name or substring match."""
    lower_query = query.lower()

    exact = [p for p in products if p.get("name", "").lower() == lower_query]
    if exact:
        return exact

    return [
        p
        for p in products
        if lower_query in p.get("name", "").lower()
        or any(lower_query in fn.lower() for fn in p.get("former_names", []))
    ]


async def do_get_lifecycle(product_name: str) -> str:
    """Fetch all products from the lifecycle API and filter client-side.

    The RHOKP lifecycle endpoint does not support server-side name
    filtering (returns HTTP 500 when a query parameter is passed),
    so we fetch the full product list and match locally.
    """
    url = f"{RHOKP_BASE}/product-life-cycles/api/v1/products/"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            return resp.text[:5000]

    products = data.get("data", [])
    matched = await _match_products(products, product_name)

    if matched:
        return json.dumps({"data": matched}, indent=2)

    available = sorted({p.get("name", "") for p in products})
    return json.dumps(
        {
            "error": f"No product matching '{product_name}'",
            "available_products": available,
        },
        indent=2,
    )


async def _run_podman(*args: str) -> tuple:
    """Run a podman command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        RHOKP_PODMAN_PATH,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


async def _wait_for_ready() -> bool:
    """Poll the Solr admin endpoint until it responds or timeout expires."""
    url = f"{RHOKP_BASE}/solr/portal/admin/ping"
    deadline = asyncio.get_event_loop().time() + RHOKP_READY_TIMEOUT
    async with httpx.AsyncClient(timeout=5) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
                pass
            await asyncio.sleep(RHOKP_READY_INTERVAL)
    return False


async def do_container_status() -> str:
    """Check the RHOKP container status."""
    rc, stdout, stderr = await _run_podman(
        "inspect",
        "--format",
        json.dumps(
            {
                "name": "{{.Name}}",
                "state": "{{.State.Status}}",
                "image": "{{.ImageName}}",
                "created": "{{.Created}}",
                "started": "{{.State.StartedAt}}",
                "ports": "{{.HostConfig.PortBindings}}",
            }
        ),
        RHOKP_CONTAINER_NAME,
    )
    if rc != 0:
        if "no such container" in stderr.lower() or "no container" in stderr.lower():
            return json.dumps(
                {
                    "exists": False,
                    "container_name": RHOKP_CONTAINER_NAME,
                    "message": (
                        f"Container '{RHOKP_CONTAINER_NAME}' does not exist. "
                        "Use 'container_start' to create and start it."
                    ),
                }
            )
        return json.dumps({"error": stderr or stdout})

    try:
        info = json.loads(stdout)
        info["exists"] = True
        info["container_name"] = RHOKP_CONTAINER_NAME
        return json.dumps(info, indent=2)
    except (ValueError, KeyError):
        return json.dumps(
            {
                "exists": True,
                "container_name": RHOKP_CONTAINER_NAME,
                "raw": stdout,
            }
        )


async def do_container_start() -> str:
    """Start the RHOKP container, creating it if necessary."""
    rc, stdout, stderr = await _run_podman(
        "inspect", "--format", "{{.State.Status}}", RHOKP_CONTAINER_NAME
    )

    if rc == 0:
        state = stdout.strip()
        if state == "running":
            return json.dumps(
                {
                    "action": "none",
                    "message": (
                        f"Container '{RHOKP_CONTAINER_NAME}' is already running."
                    ),
                }
            )
        rc, stdout, stderr = await _run_podman("start", RHOKP_CONTAINER_NAME)
        if rc != 0:
            return json.dumps({"error": f"Failed to start container: {stderr}"})
        ready = await _wait_for_ready()
        return json.dumps(
            {
                "action": "started",
                "ready": ready,
                "message": (
                    f"Container '{RHOKP_CONTAINER_NAME}' started "
                    f"(was {state})."
                    + (
                        ""
                        if ready
                        else f" Warning: Solr not ready after {RHOKP_READY_TIMEOUT}s."
                    )
                ),
            }
        )

    port_mapping = f"{RHOKP_CONTAINER_PORT}:8080"
    tls_mapping = f"{int(RHOKP_CONTAINER_PORT) + 363}:8443"
    run_args = [
        "run",
        "-d",
        "--name",
        RHOKP_CONTAINER_NAME,
        "-p",
        port_mapping,
        "-p",
        tls_mapping,
        "--pids-limit",
        RHOKP_PIDS_LIMIT,
        "--init",
    ]
    if os.path.isfile(RHOKP_KEY_FILE):
        with open(RHOKP_KEY_FILE, encoding="utf-8") as fh:
            access_key = fh.read().strip()
        if access_key:
            run_args.extend(["-e", f"ACCESS_KEY={access_key}"])
    run_args.append(RHOKP_CONTAINER_IMAGE)
    rc, stdout, stderr = await _run_podman(*run_args)
    if rc != 0:
        return json.dumps({"error": f"Failed to create container: {stderr}"})
    ready = await _wait_for_ready()
    return json.dumps(
        {
            "action": "created",
            "container_id": stdout[:12],
            "ready": ready,
            "message": (
                f"Container '{RHOKP_CONTAINER_NAME}' created from "
                f"'{RHOKP_CONTAINER_IMAGE}' and started on port "
                f"{RHOKP_CONTAINER_PORT}."
                + (
                    ""
                    if ready
                    else f" Warning: Solr not ready after {RHOKP_READY_TIMEOUT}s."
                )
            ),
        }
    )


async def do_container_stop() -> str:
    """Stop the RHOKP container."""
    rc, stdout, stderr = await _run_podman(
        "inspect", "--format", "{{.State.Status}}", RHOKP_CONTAINER_NAME
    )

    if rc != 0:
        return json.dumps(
            {"error": f"Container '{RHOKP_CONTAINER_NAME}' does not exist."}
        )

    state = stdout.strip()
    if state != "running":
        return json.dumps(
            {
                "action": "none",
                "message": (
                    f"Container '{RHOKP_CONTAINER_NAME}' is not running "
                    f"(state: {state})."
                ),
            }
        )

    rc, stdout, stderr = await _run_podman("stop", RHOKP_CONTAINER_NAME)
    if rc != 0:
        return json.dumps({"error": f"Failed to stop container: {stderr}"})
    return json.dumps(
        {
            "action": "stopped",
            "message": f"Container '{RHOKP_CONTAINER_NAME}' stopped.",
        }
    )


async def do_container_restart() -> str:
    """Restart the RHOKP container."""
    rc, _, stderr = await _run_podman(
        "inspect", "--format", "{{.State.Status}}", RHOKP_CONTAINER_NAME
    )

    if rc != 0:
        return json.dumps(
            {
                "error": (
                    f"Container '{RHOKP_CONTAINER_NAME}' does not exist. "
                    "Use 'container_start' to create it first."
                ),
            }
        )

    rc, _, stderr = await _run_podman("restart", RHOKP_CONTAINER_NAME)
    if rc != 0:
        return json.dumps({"error": f"Failed to restart container: {stderr}"})
    ready = await _wait_for_ready()
    return json.dumps(
        {
            "action": "restarted",
            "ready": ready,
            "message": (
                f"Container '{RHOKP_CONTAINER_NAME}' restarted."
                + (
                    ""
                    if ready
                    else f" Warning: Solr not ready after {RHOKP_READY_TIMEOUT}s."
                )
            ),
        }
    )


async def handle_list_tools(_ctx, _params):  # pylint: disable=unused-argument
    """Return the list of available MCP tools."""
    return types.ListToolsResult(tools=TOOLS)


async def _do_search(args: Dict) -> str:
    """Handle keyword search."""
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
    return json.dumps(results, indent=2)


async def _do_semantic(args: Dict) -> str:
    """Handle semantic search."""
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
    return json.dumps(results, indent=2)


async def _do_hybrid(args: Dict) -> str:
    """Handle hybrid search."""
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
    return json.dumps(results, indent=2)


async def _do_solution(args: Dict) -> str:
    """Handle get_solution."""
    sid = re.sub(r"[^0-9]", "", args["solution_id"])
    text = await do_get_page(f"solutions/{sid}/index.html")
    return text or f"Solution {sid} not found in this RHOKP instance."


async def _do_article(args: Dict) -> str:
    """Handle get_article."""
    aid = re.sub(r"[^0-9]", "", args["article_id"])
    text = await do_get_page(f"articles/{aid}/index.html")
    return text or f"Article {aid} not found in this RHOKP instance."


async def _do_lifecycle(args: Dict) -> str:
    """Handle get_product_lifecycle."""
    return await do_get_lifecycle(args["product_name"])


_TOOL_HANDLERS = {
    "search": _do_search,
    "semantic_search": _do_semantic,
    "hybrid_search": _do_hybrid,
    "get_solution": _do_solution,
    "get_article": _do_article,
    "get_product_lifecycle": _do_lifecycle,
    "container_status": lambda _args: do_container_status(),
    "container_start": lambda _args: do_container_start(),
    "container_stop": lambda _args: do_container_stop(),
    "container_restart": lambda _args: do_container_restart(),
}


async def handle_call_tool(
    _ctx, params: types.CallToolRequestParams
):  # pylint: disable=unused-argument
    """Dispatch an MCP tool call to the appropriate handler."""
    name = params.name
    args = params.arguments or {}

    try:
        handler = _TOOL_HANDLERS.get(name)
        if not handler:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )
        text = await handler(args)

    except httpx.HTTPStatusError as exc:
        text = f"HTTP error {exc.response.status_code}: {exc.response.text[:500]}"
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=True,
        )
    except httpx.ConnectError:
        text = (
            f"Cannot connect to RHOKP at {RHOKP_BASE}. "
            "Use the 'container_status' tool to check whether the "
            "RHOKP container is running, or 'container_start' to "
            "launch it."
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
        "'get_article' when you have a specific document ID. "
        "Container management: use 'container_status' to check the RHOKP "
        "backend, 'container_start' to launch it, 'container_stop' to "
        "shut it down, and 'container_restart' to restart it."
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
