# synthetic cfo MCP server

Generate forensic-grade synthetic ERP data, and its labelled fraud answer key,
from inside Claude, Cursor, VS Code, or any MCP-compatible client. No real data
anywhere in the process, and no language model ever generates the financial data:
the client issues a validated request and the deterministic synthetic cfo engine
executes it. Same seed plus same configuration regenerates a byte-identical
package.

## Setup on any plan (hosted mode, three steps)

The server runs against app.syntheticcfo.com with your API key, so it works on
every plan without the engine's source. Download it from
https://app.syntheticcfo.com/sdk/mcp_server.zip (the single-file Python client
is inside the zip), then:

1. `pip install mcp` (the MCP protocol package; Python 3.10 or newer).
2. Create an API key in the app (menu, your email, Account, API access) and set
   `SCFO_API_KEY=scfo_...` in the environment your MCP client launches the
   server with.
3. Register the server with your client, for example in Claude Desktop's
   `claude_desktop_config.json`:

```json
{"mcpServers": {"synthetic-cfo": {
  "command": "python", "args": ["-m", "mcp_server"],
  "cwd": "/path/to/the/unzipped/folder",
  "env": {"SCFO_API_KEY": "scfo_..."}}}}
```

Every tool then runs on the platform under your plan's limits, downloads the
package to `SCFO_MCP_OUTPUT_DIR` (default: a synthetic_cfo_mcp folder in your
temp directory) and works on the extracted files. Without a key the server
expects the engine source beside it (self-hosted mode).

## What it gives an agent

A generic synthetic-data tool can produce plausible-looking rows. This gives an
agent a complete, accounting-correct ERP world **and the ground truth about the
fraud hidden inside it**, so it can build or score a detector against the answer,
not just eyeball the data.

## Tools

- **list_capabilities** - the platforms, modules, industries, jurisdictions,
  fraud levels and volume limits you can request.
- **generate_dataset** - generate a four-layer package (executive brief, audit
  evidence with the ground-truth fraud answer key, a SQLite database with full
  referential integrity, and source workbooks that mirror the real ERP tables).
  Returns the package location and a summary. Supports multi-year fraud arcs.
- **inspect_package** - tables and row counts, the answer-key breakdown by
  module / control / vector, the financial-statement headline, tax filing.
- **query_answer_key** - the ground-truth fraud labels as structured rows
  (module, control id, fraud vector, source table, exact record id), so you can
  grade a detector against the truth. Filter by module or control.
- **read_table** - read rows from any table in the generated database, with an
  optional single-column equality filter.
- **reproduce** - regenerate from an exact config + seed and report the SHA-256
  file hashes, so you can verify byte-identical reproduction.

## Install

The server drives the engine in this repository directly, so it needs the
repo's Python environment plus the MCP SDK:

```bash
pip install -r requirements.txt
pip install -r mcp_server/requirements.txt
```

## Connect a client

The server speaks MCP over stdio. Point your client at it.

**Claude Desktop** (`claude_desktop_config.json`) or **Cursor**
(`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "synthetic-cfo": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "/absolute/path/to/synthetic-cfo-platform",
      "env": { "SCFO_MCP_OUTPUT_DIR": "/absolute/path/for/generated/packages" }
    }
  }
}
```

`SCFO_MCP_OUTPUT_DIR` is optional; it defaults to a temp directory. Restart the
client and the six tools appear.

## Example conversation

> "Generate a German SAP manufacturer with high fraud, 1,000 rows, including
> fixed assets. Then show me the fraudulent invoices and the answer key so I can
> test a detector."

The client calls `generate_dataset`, then `query_answer_key` and `read_table`,
and hands you the package plus the labelled truth, all without leaving the editor.

## Guarantees

- No real company data is ever used.
- No language model generates any financial data; generation is deterministic
  Python from accounting rules.
- Same seed + config = byte-identical package (`reproduce` proves it).

## Licence and plan terms

This local MCP server runs the deterministic engine on your machine, so it does
not itself enforce account limits. It is provided under the **Developer plan
terms**: evaluation and non-commercial use, the core four business cycles (P2P,
O2C, CE, R2R) across the three platforms, and modest dataset sizes. Using the
generated data in a commercial product, service, or client engagement, or at the
larger volumes and full module set the hosted plans offer, requires a paid plan.
See the pricing on the hosted app for what each plan includes.
