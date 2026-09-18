# COROS Running Vault MCP v1.0

A small private MCP + SQLite cache for selected COROS running data.

## Why this design

COROS already provides an official OAuth MCP at `https://mcp.coros.com/mcp`.
This project does **not** store your COROS password or replace that official connection.

Instead, ChatGPT can:
1. Read live COROS data through the official COROS MCP.
2. Normalize selected fields.
3. Store them in this private MCP on your own server.
4. Query longer-term local trends without repeatedly fetching everything from COROS.

## Tools

Write/cache:
- `coros_vault_upsert_runs`
- `coros_vault_upsert_laps`
- `coros_vault_upsert_daily`
- `coros_vault_store_fitness`
- `coros_vault_store_raw`

Read:
- `coros_vault_recent_runs`
- `coros_vault_run_laps`
- `coros_vault_daily`
- `coros_vault_summary`
- `coros_vault_status`

## Docker

```bash
docker build -t coros-vault-mcp .
docker run -d \
  --name coros-vault-mcp \
  --restart unless-stopped \
  --env-file .env \
  -v /opt/coros-vault/data:/data \
  -p 127.0.0.1:8100:8100 \
  coros-vault-mcp
```

The MCP path is:

`http://127.0.0.1:8100/<MCP_PATH_TOKEN>/mcp`

Expose it through your existing Caddy HTTPS reverse proxy, not directly on port 8100.
