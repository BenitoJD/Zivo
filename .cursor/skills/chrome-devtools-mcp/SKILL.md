---
name: chrome-devtools-mcp
description: Debug zivo in real Chrome via chrome-devtools-mcp — console, network, snapshots, Lighthouse, performance traces. Use for prod-only UI bugs, mixed-content/CORS, PDF viewer issues, or when DevTools-level inspection beats Playwright scripts.
---

# Chrome DevTools MCP (zivo)

Drive a real Chrome instance through the [chrome-devtools-mcp](https://www.npmjs.com/package/chrome-devtools-mcp) MCP server. Prefer this over guessing from code when the bug is browser-only (prod CSS, MIME types, presigned URLs, pdf.js warnings).

## Setup

Project config: `.cursor/mcp.json` (committed). After clone or edit, reload Cursor (**Settings → MCP** or restart).

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
    }
  }
}
```

If `chrome-devtools` is also defined in `~/.cursor/mcp.json`, keep it in **one** file only to avoid duplicate servers in Cursor.

Optional flags (append to `args`): `--headless`, `--browser-url http://127.0.0.1:9222`, `--auto-connect` (Chrome 144+ with remote debugging). See [package docs](https://www.npmjs.com/package/chrome-devtools-mcp).

## MCP server name

In `CallMcpTool`, the server id is usually `chrome-devtools` or `user-chrome-devtools`. If a call fails with “server not found”, list enabled MCP servers in Cursor settings or read tool schemas under the project `mcps/` folder before calling.

**Always read the tool JSON schema** in `mcps/<server>/tools/<tool>.json` before `CallMcpTool`.

## When to use what

| Goal | Tool |
|------|------|
| Console errors, mixed content, failed fetches | **chrome-devtools-mcp** (`list_console_messages`, `list_network_requests`) |
| Lighthouse / perf trace | **chrome-devtools-mcp** |
| Prod vs local repro in real Chrome | **chrome-devtools-mcp** |
| Scripted E2E + CI artifacts | **Playwright** (`e2e-testing` skill) |
| PR screenshot/video upload | `pr-screenshot-workflow` (after capturing media) |
| Quick in-IDE browse without full DevTools | `cursor-ide-browser` (fallback only) |

## zivo URLs

| Environment | Frontend | API |
|-------------|----------|-----|
| Local (`./scripts/dev.sh start`) | `http://localhost:5273` | `http://localhost:8100` |
| Production | `https://zivo.example` | `https://api.zivo.example` |
| Object storage (presigned) | — | `https://s3.zivo.example` |

Local dev users: `dev` / `admin` (passwords from `SEED_DEV_PASSWORD`, `SEED_ADMIN_PASSWORD` in env). See `zivo-dev` skill.

## Core workflow

1. **`list_pages`** — see open tabs; **`select_page`** if needed.
2. **`navigate_page`** — `{ "type": "url", "url": "https://zivo.example" }` (or local URL).
3. **`take_snapshot`** — a11y tree with `uid` per element; re-snapshot after each navigation or major DOM change.
4. Interact: **`click`**, **`fill`**, **`press_key`**, **`hover`** using `uid` from the latest snapshot.
5. **`wait_for`** — text or conditions before asserting.
6. Debug: **`list_console_messages`** (filter `types: ["error", "warn"]`), **`list_network_requests`** (filter `resourceTypes: ["fetch", "xhr"]`), **`get_network_request`** for one URL.
7. Visual proof: **`take_screenshot`**; deep layout: snapshot + **`evaluate_script`** (JSON-serializable return only).

Do not reuse stale `uid` values across snapshots.

## zivo-specific checks

**PDF viewer blank / wasm warnings**

- Console: pdf.js `wasmUrl`, JBig2, MIME errors on `.mjs` assets.
- Network: document file redirect → presigned URL host must be `https://s3.zivo.example`, not internal `minio.zivo.svc` or `http://`.

**Mixed content**

- `list_console_messages` with `types: ["error"]` — blocked mixed-content on MinIO/http URLs.
- `list_network_requests` — compare presigned `Location` / fetch URL scheme vs page `https://`.

**Modal / glass bleed (prod)**

- `take_screenshot` on delete/auth sheets over an open PDF; compare prod vs local.

**Indexing stuck**

- Usually worker/API — use `zivo-dev` logs first; use DevTools only to confirm UI polling/API errors.

## Performance & audits

- **`performance_start_trace`** → reproduce → **`performance_stop_trace`** → **`performance_analyze_insight`**
- **`lighthouse_audit`** for accessibility/performance categories on workspace routes.

## Reporting

Include in findings:

- URL and viewport (`emulate` / `resize_page` if relevant)
- Console errors (verbatim)
- Failing request URL, status, and response snippet
- Screenshot path or description
- Whether reproduced on prod, local, or both

For PR evidence, save screenshots under `output/playwright/<task-slug>/` and run `pr-screenshot-workflow`.

## Related skills

- `zivo-dev` — start stack, ports, worker logs
- `e2e-testing` — repeatable Playwright tests
- `pr-screenshot-workflow` — upload captured media to PR body
- `responsive-ui-audit` — breakpoint matrix (can use either Playwright or this MCP)
