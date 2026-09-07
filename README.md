# syntheticcfo

The Python client, command line and MCP server for **synthetic cfo**:
forensic-grade synthetic ERP data for SAP ECC, SAP S/4HANA and Oracle Cloud,
generated entirely from accounting rules, with a labelled fraud answer key.

No real company data is ever used. No language model generates any figure: the
engine is deterministic Python, and the same seed and configuration regenerate a
package byte for byte.

- Product and free sample: https://app.syntheticcfo.com/sample
- The worked proof (real detectors scored on this data, including the one that
  fails): https://app.syntheticcfo.com/proof
- How to evaluate it in an afternoon: https://app.syntheticcfo.com/evaluate
- Package on the index: https://pypi.org/project/syntheticcfo/

## Install

```bash
pip install syntheticcfo
```

Standard library only, no dependencies. Python 3.8 or newer.

## One command

Create an API key in the app (menu, your email, Account, API access), then:

```bash
syntheticcfo generate --key scfo_YOURKEY --platform oracle --industry retail --fraud high --rows 600
```

It builds the world, waits, and saves the package zip where you stand. Add
`--lab` to also save the JSON-lines lab export, `--seed 42` to make the world
reproducible on purpose, and `--evaluation-mode` to withhold the ground-truth
marker columns from the shipped data. `python -m syntheticcfo` does the same
thing everywhere.

## From Python

```python
from syntheticcfo import Client

c = Client(api_key="scfo_...")            # or set SCFO_API_KEY
job = c.generate(
    platform="SAP_ECC",                   # SAP_ECC | SAP_S4HANA | ORACLE
    industry="MANUFACTURING",
    fraud="medium",                       # none | low | medium | high
    rows=1500,
    modules=["P2P", "O2C", "CE", "R2R"],  # subset of P2P O2C CE R2R HR MM FA
    jurisdiction="DE",
    seed=42,
    wait=True,
)
package = job.download("out/")            # the four-layer package (zip)
lab = job.lab_export("out/")              # JSONL tables + answer_key.jsonl + dataset card
print(job.insights())                     # what was planted, by control
```

## What you get

Every dataset is a complete four-layer package: an executive brief with the
whitepaper and articulated financial statements, an audit evidence layer with
the forensic report and the ground-truth answer key, a SQLite database with
full referential integrity, and source workbooks mirroring the real ERP table
architecture. Seven cycles (purchase to pay, order to cash, cash and bank,
record to report, payroll, inventory, fixed assets), ten jurisdictions, more
than 25 fraud schemes each planted beside innocent look-alikes so a detector
can be measurably wrong.

## MCP server

`mcp_server/` lets Claude, Cursor, VS Code or any MCP client generate datasets,
download packages and read answer keys in plain language. Three-step setup in
[mcp_server/README.md](mcp_server/README.md).

## Plans

The free Developer plan needs no request: all three platforms, the core four
cycles, up to 1,000 rows, 5 datasets a month, with the answer key. Paid plans
add the remaining modules, volume, multi-year arcs, the blind evaluation track,
the intercompany group and the hosted ERP environment. Plan limits apply
exactly as in the app; over-plan requests are refused with an honest message.

## Licence

The code in this repository (the client, the command line and the MCP server)
is MIT licensed; see LICENSE. The platform, the generated data and their terms
of use are separate: https://app.syntheticcfo.com/terms
