# RHOKP MCP Server

Model Context Protocol (MCP) server that exposes a local [Red Hat Offline Knowledge Portal (RHOKP)](https://github.com/redhatofficial/rhokp) instance as AI-consumable tools. Enables LLM-powered assistants to search Red Hat knowledgebase solutions, articles, documentation, CVEs, and product lifecycle data — all without internet access.

## Overview

RHOKP bundles Red Hat's public knowledge base into a self-contained, offline-capable portal backed by Apache Solr. This MCP server sits in front of that portal and translates MCP tool calls into Solr queries and HTTP page fetches, so any MCP-compatible client (Claude Code, Claude Desktop, etc.) can query it directly.

**Tools exposed:**

| Tool | Description |
|---|---|
| `hybrid_search` | Combined keyword + semantic search (recommended default) |
| `search` | Full-text keyword search — best for exact terms, error messages, solution/CVE IDs |
| `semantic_search` | Vector search over ~1.2M RAG chunks — best for natural language questions |
| `get_solution` | Retrieve a specific solution by numeric ID |
| `get_article` | Retrieve a specific article by numeric ID |
| `get_product_lifecycle` | Get lifecycle/support dates for a Red Hat product |

## Prerequisites

- **Python 3.9+**
- **RHOKP instance** — a running RHOKP container (see [RHOKP project](https://github.com/redhatofficial/rhokp) for setup)
- **pip packages** — `httpx`, `mcp` (see `requirements.txt`)

## Quick Start

1. **Install dependencies**:
   ```bash
   pip3 install -r requirements.txt
   ```

2. **Start RHOKP** (if not already running):
   ```bash
   podman run -d -p 8080:8080 rhokp:latest
   ```

3. **Run the MCP server** (standalone test):
   ```bash
   python3 rhokp_mcp_server.py
   ```

   The server communicates over stdio (stdin/stdout) using the MCP protocol — it is not meant to be used interactively. See the sections below for configuring it with an MCP client.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `RHOKP_BASE_URL` | `http://localhost:8080` | Base URL of the RHOKP instance |

### Claude Code

Add the server to your Claude Code MCP configuration (`.claude/settings.json` or project-level):

```json
{
  "mcpServers": {
    "rhokp": {
      "command": "python3",
      "args": ["/path/to/rhokp_mcp_server.py"],
      "env": {
        "RHOKP_BASE_URL": "http://localhost:8080"
      }
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "rhokp": {
      "command": "python3",
      "args": ["/path/to/rhokp_mcp_server.py"],
      "env": {
        "RHOKP_BASE_URL": "http://localhost:8080"
      }
    }
  }
}
```

## Usage Examples

Once configured, the MCP client will automatically discover the tools. Example prompts:

```
Search for solutions about "CrashLoopBackOff in OpenShift"

What are the lifecycle dates for Red Hat Enterprise Linux 9?

Look up solution 7079626

Find articles about configuring etcd encryption on OpenShift
```

### Tool Selection Guide

- **`hybrid_search`** — Start here. Combines keyword precision with semantic understanding. Works well for most queries.
- **`search`** — Use when you have an exact error message, solution ID (e.g. `7079626`), or CVE number. Pure keyword matching.
- **`semantic_search`** — Use for broad natural language questions (e.g. "how do I resize a persistent volume"). May surface relevant content that keyword search misses.
- **`get_solution` / `get_article`** — Use when you already know the document ID and want the full text.
- **`get_product_lifecycle`** — Use for support date questions (e.g. "when does RHEL 8 go end-of-life?").

### Filtering Results

Both `search` and `semantic_search`/`hybrid_search` support filtering:

- **`search`** — Filter by `document_kind`: `solution`, `article`, `documentation`, `errata`
- **`semantic_search` / `hybrid_search`** — Filter by `product`: e.g. `openshift_container_platform`, `red_hat_enterprise_linux`

All search tools accept a `rows` parameter (1–20, default 5) to control result count.

## How It Works

1. The MCP client sends a tool call (e.g. `hybrid_search` with a query string).
2. The server translates it into an HTTP request against the RHOKP Solr API.
3. For `get_solution`/`get_article`, it fetches the HTML page and extracts the main content text.
4. For `get_product_lifecycle`, it queries the RHOKP lifecycle API.
5. Results are returned as structured JSON (search tools) or plain text (content tools).

## Troubleshooting

### Cannot connect to RHOKP

**Problem**: Tool calls return "Cannot connect to RHOKP at ..."

**Solution**:
- Verify the RHOKP container is running: `podman ps | grep rhokp`
- Check the URL is correct: `curl http://localhost:8080`
- If RHOKP is on a different host/port, set `RHOKP_BASE_URL`

### Search returns no results

**Problem**: Searches return 0 results for queries that should match

**Solution**:
- Verify the Solr cores are populated: `curl "http://localhost:8080/solr/portal/select?q=*:*&rows=0"`
- Try a broader query or switch between `search` and `hybrid_search`
- Check that the RHOKP data import completed successfully

### Solution/article not found

**Problem**: `get_solution` or `get_article` returns "not found"

**Solution**:
- Verify the ID is correct (numeric only)
- The content may not be included in this RHOKP build — try searching for it first
- Check the RHOKP instance includes the expected content set

### MCP client does not discover tools

**Problem**: The MCP client shows no tools from this server

**Solution**:
- Verify the server path in the MCP configuration is correct
- Check that dependencies are installed: `pip3 show httpx mcp`
- Test the server manually: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{},"clientInfo":{"name":"test","version":"0.1"},"protocolVersion":"2024-11-05"}}' | python3 rhokp_mcp_server.py`

## Requirements

- Python 3.9+ with `httpx` and `mcp` packages
- A running RHOKP instance accessible via HTTP
