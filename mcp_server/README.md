# synthetic cfo MCP server

Generate forensic-grade synthetic ERP data, and its labelled fraud answer key,
from inside Claude, Cursor, VS Code, or any MCP-compatible client. No real data
anywhere in the process, and no language model ever generates the financial
data: the agent sends a request, the platform validates it and the
deterministic synthetic cfo engine executes it. The same seed and configuration
on the same engine version regenerate a byte-identical package; its certificate
names the engine, and on another engine version the same seed can build a
different dataset.

It generates data in the shape of SAP ECC, SAP S/4HANA and Oracle Cloud, plus
the two combined environments (the SAP migration pair and the intercompany
group) on the Team plan and above. The worked proof, three detectors scored
against the answer key, is at
[app.syntheticcfo.com/proof](https://app.syntheticcfo.com/proof)

## Hosted connector (no install)

The same seven tools run on the platform itself at

```
https://app.syntheticcfo.com/mcp
```

over Streamable HTTP. A client signs in with your synthetic cfo account
(OAuth: a synthetic cfo page asks you to sign in and to allow the app), or
sends an API key as `Authorization: Bearer scfo_...`. Your plan's limits
apply as in the app. Connected apps are listed, and can be disconnected,
under Account in the app.

- Claude (claude.ai and Claude Desktop): Settings, Connectors, Add custom
  connector, then paste the address.
- Claude Code: `claude mcp add --transport http synthetic-cfo https://app.syntheticcfo.com/mcp`,
  then `/mcp` to sign in.
- Cursor: add `{"mcpServers": {"synthetic-cfo": {"url": "https://app.syntheticcfo.com/mcp"}}}`
  to `~/.cursor/mcp.json`, or use the Add to Cursor link on the connect page.
- ChatGPT, Grok, VS Code and other clients: add a remote MCP server with the
  address above and sign in when asked.

On the hosted connector the read tools (`inspect_package`,
`query_answer_key`, `read_table`, `get_deliverable`) take the `job_id` of one
of your datasets instead of a folder, and `generate_dataset` returns the job
id, a summary and a link that opens the dataset in the app (signing you in
first when needed). Your datasets' documents are also offered as MCP resources,
`scfo://jobs/<job_id>/<kind>`. Step by step: https://app.syntheticcfo.com/connect

## Run it on your machine (stdio)

The rest of this page sets up the same tools as a local server that your
client launches.

## Run it in a container

The Dockerfile at the root of this repository builds the server and starts it
on stdio

```bash
docker build -t syntheticcfo-mcp .
docker run --rm -i -e SCFO_API_KEY syntheticcfo-mcp
```

Set `SCFO_API_KEY` to generate. Without it the server still starts and lists its seven tools, and the tools that generate
answer with an error asking for the key.

## Setup on any plan (three steps)

The server runs against app.syntheticcfo.com with your API key, so it needs
nothing but the `mcp` package and the Python client. Take it from this
repository, or as a zip from https://app.syntheticcfo.com/sdk/mcp_server.zip
(the zip carries the client inside), then:

1. `pip install "mcp>=1.21,<2" syntheticcfo` (Python 3.10 or newer; the server
   is built on the 1.x line of the `mcp` package and does not start on 2.x).
2. Create an API key in the app (menu, your email, Account, API access; a free
   account works: https://app.syntheticcfo.com/?signup=1) and set
   `SCFO_API_KEY=scfo_...` in the environment your MCP client launches the
   server with.
3. Point your client at `python` with the full path to `mcp_server/server.py`
   as its argument, so the server starts from whatever folder the client
   launches it in, for example in Claude Desktop's `claude_desktop_config.json`
   or Cursor's `.cursor/mcp.json`:

```json
{"mcpServers": {"synthetic-cfo": {
  "command": "python", "args": ["/path/to/the/folder/mcp_server/server.py"],
  "env": {"SCFO_API_KEY": "scfo_..."}}}}
```

Restart the client and the seven tools appear. Generation runs on the platform
under your plan's limits; each package is downloaded to `SCFO_MCP_OUTPUT_DIR`
(optional; default: a synthetic_cfo_mcp folder in your temp directory) and the
tools work on the extracted files. Keep the key set: without it the tools that
generate look for the engine beside the server instead, and the engine is not
published, so they answer with an error asking for the key.

## What it gives an agent

A generic synthetic-data tool can produce plausible-looking rows. This gives an
agent a complete, accounting-correct ERP world **and the ground truth about the
fraud hidden inside it**, so it can build or score a detector against the answer,
not just eyeball the data.

## Tools

- **list_capabilities** - what you can request: the platforms and the two
  combined environments, the modules, industries, jurisdictions and regions,
  fraud levels, volume tiers and size bands, and your plan's limits (its scale
  on each platform).
- **generate_dataset** - generate a four-layer package: an executive brief,
  audit evidence with the ground-truth fraud answer key, a SQLite database in
  which every key between the tables it ships resolves (measured and printed in
  the package, where references to masters of modules you did not choose are
  listed too), and source workbooks that mirror the real ERP tables. Returns
  the package location, the job id and a summary, with the platform's notes
  (such as the scale it chose when no `num_rows` was given) under `warnings`.
  It takes what the app's Advanced panel sets, evaluation mode, operational
  noise, a region, a size band and a foreign-currency share included.
  Evaluation mode is on unless `evaluation_mode` is false, which asks for the
  annotated form (the annotations and the marker columns on the rows).
  Multi-year arcs, the blind evaluation track, the migration pair and the
  intercompany group need the Team plan or above.
- **inspect_package** - the package's shape and legs, the one database that
  represents it (tables and row counts), the answer-key breakdown by module /
  control / vector (and by entity, year or leg where the key carries them),
  the financial-statement headline and tax filing.
- **query_answer_key** - the ground-truth fraud labels as structured rows
  (module, control id, fraud vector, source table, exact record id), so you can
  grade a detector against the truth. Filter by module or control; name a `leg`
  on a multi-leg package to read that leg's own key.
- **read_table** - read rows from any table in the generated database, with an
  optional single-column equality filter; name a `leg` to read that leg's own
  database.
- **reproduce** - build a package again and report the engine version and the
  SHA-256 file hashes from its certificate (a multi-leg archive's from the
  archive certificate at its root). Given the `job_id` of an earlier
  generation, it is the platform's Reproduce: free on the engine version that
  built the job, where the hashes match the earlier run's, and on another
  engine a new dataset that counts as one generation on a metered plan. Given
  the `config` and `seed` from an earlier result's `reproduce` field instead,
  it is a new generation under your plan, with the same hashes as the earlier
  run's on the same engine version.

- **get_deliverable** - read one of the package's documents in the chat: the
  executive brief (the whitepaper with the reproducibility certificate), the
  forensic audit report, the financial statements with their workpaper, the
  control results, the answer-key summary, the tax filing, the realism report,
  a module manifest, or one sheet of a source workbook. PDFs come back as their
  text (20,000 characters at a time, with an offset for the next part) and
  tables as rows (50 by default, 200 at most). `kind` "list", the default,
  names the documents the package holds. Reading a PDF's text needs the
  `pypdf` package (`pip install pypdf`); the hosted connector has it.

## Package shapes

Which database represents a package and which files form its one answer key
is decided by the engine's `engine/layout.py` and `engine/labels.py`, and the
tools read those (the zip ships byte-identical copies as `_layout.py` and
`_labels.py` for hosted mode, where the engine is not present): a single
world's own database; the consolidated database of a multi-year arc (every
year, `FISCAL_YEAR` on each row) with one key per year read together; the
merged database and merged root key of an intercompany group (both
subsidiaries and the intercompany layer, each label carrying its entity); the
cross-year group database of a group arc; and, for a migration pair, which has
no merged database or key, the SAP ECC leg, with the S/4HANA leg reachable by
`leg`. `inspect_package` names the shape, the legs and the scope of what it
reports.

## Example conversation

> "Generate a German SAP manufacturer with high fraud, 600 rows, purchase to
> pay and order to cash. Then show me the fraudulent invoices and the answer key
> so I can test a detector."

The client calls `generate_dataset`, then `query_answer_key` and `read_table`,
and hands you the package plus the labelled truth, all without leaving the editor.

## Guarantees

- No real company data is ever used.
- No language model generates any financial data; generation is deterministic
  Python from accounting rules.
- The same seed and configuration on the same engine version give a
  byte-identical package; `reproduce` reports the engine and the hashes so you
  can check it.

## Licence

The server generates on app.syntheticcfo.com under your account, whose plan
sets its limits. The code of the server and of the Python client is MIT
licensed (the LICENSE file in the repository and in the zip). The data it
generates comes from the platform under your plan and the Terms at
https://app.syntheticcfo.com/terms.
