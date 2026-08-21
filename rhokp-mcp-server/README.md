# RHOKP MCP Server

Model Context Protocol (MCP) server that exposes a local [Red Hat Offline Knowledge Portal (RHOKP)](https://docs.redhat.com/en/documentation/red_hat_offline_knowledge_portal/1/install-deploy_the_red_hat_offline_knowledge_portal_using_podman_desktop) instance as AI-consumable tools. Enables LLM-powered assistants to search Red Hat knowledgebase solutions, articles, documentation, CVEs, and product lifecycle data — all without internet access.

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
| `container_status` | Check if the RHOKP backend container is running |
| `container_start` | Start the RHOKP container (creates it if needed) |
| `container_stop` | Stop the RHOKP container gracefully |
| `container_restart` | Restart the RHOKP container |

## Prerequisites

- **Python 3.9+**
- **RHOKP instance** — a running RHOKP container (see the [official install guide](https://docs.redhat.com/en/documentation/red_hat_offline_knowledge_portal/1/install-deploy_the_red_hat_offline_knowledge_portal_using_podman_desktop) for setup and license instructions)
- **pip packages** — `httpx`, `mcp` (see `requirements.txt`)

## Quick Start

1. **Install dependencies**:
   ```bash
   pip3 install -r requirements.txt
   ```

2. **Start RHOKP** (if not already running):
   ```bash
   podman run -d --name rhokp \
     -p 8080:8080 -p 8443:8443 \
     --pids-limit 8192 --init \
     registry.redhat.io/offline-knowledge-portal/rhokp-rhel9:1784655565
   ```
   If your RHOKP build requires an access key, either pass it via `-e ACCESS_KEY=<key>` or write it to `~/.rhokp_key.txt` (the server reads it automatically on `container_start`).

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
| `RHOKP_PODMAN_PATH` | `podman` | Path to the podman binary |
| `RHOKP_CONTAINER_NAME` | `rhokp` | Name for the RHOKP container |
| `RHOKP_CONTAINER_TAG` | `1784655565` | Image tag (build ID) to use |
| `RHOKP_CONTAINER_IMAGE` | `registry.redhat.io/offline-knowledge-portal/rhokp-rhel9:<tag>` | Container image (constructed from tag by default) |
| `RHOKP_CONTAINER_PORT` | `8080` | Host port to bind (TLS port is automatically mapped to port+363, e.g. 8443) |
| `RHOKP_READY_TIMEOUT` | `120` | Seconds to wait for Solr readiness after starting the container |
| `RHOKP_READY_INTERVAL` | `3` | Polling interval in seconds for the readiness check |
| `RHOKP_PIDS_LIMIT` | `8192` | Container pids limit (RHOKP runs multiple services internally) |
| `RHOKP_KEY_FILE` | `~/.rhokp_key.txt` | Path to a file containing the RHOKP access key (if required by your RHOKP build) |

### Claude Code

Add the server to your Claude Code MCP configuration (`.claude/settings.json` or project-level):

```json
{
  "mcpServers": {
    "rhokp": {
      "command": "python3",
      "args": ["/path/to/rhokp_mcp_server.py"],
      "env": {
        "RHOKP_BASE_URL": "http://localhost:8080",
        "RHOKP_PODMAN_PATH": "/opt/podman/bin/podman"
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
        "RHOKP_BASE_URL": "http://localhost:8080",
        "RHOKP_PODMAN_PATH": "podman"
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

### Container Management

The server can manage the RHOKP backend container directly. If a search tool fails because the backend is unreachable, the error message will suggest using the container tools.

```
Check if the RHOKP container is running

Start the RHOKP container

Stop the RHOKP backend

Restart the RHOKP container
```

The container tools use `podman` to manage the container. Set `RHOKP_PODMAN_PATH` if podman is not on your PATH. When `container_start` is called and no container exists, it creates one from the configured image with the configured port mapping.

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
