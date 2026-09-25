# lemonde-mcp

Local MCP server (stdio) that gives an AI agent (Claude, VS Code Copilot, and others) access to **your own** Le Monde subscription. It also offers optional headline triage with Jev (TypeSafe).

| Tool | What it does |
|---|---|
| `list_sections` | Available RSS sections (`une`, `en_continu`, `international`, `economie`, `pixels`, `en_english`, ...) |
| `get_headlines(section, limit)` | Latest headlines from RSS |
| `search_headlines(query, sections, limit)` | Keyword search over recent headlines |
| `get_article(url)` | Full article text using your subscriber cookie (`truncated: true` = paywall hit, so refresh the cookie) |
| `triage_news(interests, sections, limit)` | Jev scores each headline for relevance and importance, assigns a topic, and returns them sorted |

## Setup (macOS)

```zsh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
scripts/bootstrap_cookie.sh          # paste your lemonde.fr Cookie header
scripts/bootstrap_typesafe_key.sh    # optional, only for triage_news
```

Getting the cookie: log in on lemonde.fr, open DevTools, go to Network, and reload. Click the page request, then copy the `cookie` request header. It is stored in the macOS Keychain (`lemonde-mcp`/`cookie`). Re-run the script when the session expires. As alternatives to the Keychain, you can set `LEMONDE_COOKIE` and `TYPESAFE_API_KEY` as environment variables.

## Connect

**Claude Code**
```zsh
claude mcp add lemonde -- "$PWD/.venv/bin/python" "$PWD/server.py"
```

**Claude Desktop**: `~/Library/Application Support/Claude/claude_desktop_config.json`
```json
{
  "mcpServers": {
    "lemonde": {
      "command": "/path/to/lemonde-mcp/.venv/bin/python",
      "args": ["/path/to/lemonde-mcp/server.py"]
    }
  }
}
```

**VS Code**: `.vscode/mcp.json`
```json
{
  "servers": {
    "lemonde": { "type": "stdio", "command": "/path/to/lemonde-mcp/.venv/bin/python", "args": ["/path/to/lemonde-mcp/server.py"] }
  }
}
```

## Notes
- TLS uses the OS trust store (`truststore`), which also works behind TLS-inspecting proxies. Override with `LEMONDE_CA_BUNDLE`.
- The cookie is only sent to `*.lemonde.fr`. Redirects are validated hop by hop.
- For personal use with your own subscription only. Do not redistribute the content.
