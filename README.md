# syntheticcfo

See the worked proof first, three detectors scored against the answer key, at
[app.syntheticcfo.com/proof](https://app.syntheticcfo.com/proof)

synthetic cfo generates SAP and Oracle ledgers from accounting rules, with fraud
planted, every planted record written down at the moment it is planted, and
innocent look-alikes beside nearly every scheme, so fraud detection can be
trained and measured without a single real record.

This repository holds its Python client, its command line and its MCP server,
for data in the shape of SAP ECC, SAP S/4HANA and Oracle Cloud. No real company
data is ever used, and no language model generates any figure: the engine is
deterministic Python. The same seed and configuration on the same engine
version regenerate a package byte for byte; the certificate in each package
you generate names its engine, and on another engine version the same seed can
build a different dataset.

- Product and free sample: https://app.syntheticcfo.com/sample
- The worked proof (three detectors, from generic anomaly detection to rules
  written for the schemes, scored against the answer key):
  https://app.syntheticcfo.com/proof
- How to evaluate it in an afternoon: https://app.syntheticcfo.com/evaluate
- Package on the index: https://pypi.org/project/syntheticcfo/
- The sample's tables and answer key as JSON lines, built from the same seed
  and configuration (the listing names the engine version it was built on):
  https://huggingface.co/datasets/syntheticcfo/synthetic-cfo-sample
- Starter notebook (loads the sample and scores two detectors against its
  answer key in the browser):
  https://www.kaggle.com/code/syntheticcfo/find-the-fraud-then-score-yourself
- The leaderboard, with our own detectors as the baselines; every plan, the
  free one included, can generate each official track from the app's Benchmark
  tab, one generation each: https://app.syntheticcfo.com/leaderboard

## Install

```bash
pip install syntheticcfo
```

Standard library only, no dependencies. Python 3.8 or newer. If your network
blocks the package index, the platform serves the current wheel itself; the
exact command is on https://app.syntheticcfo.com/evaluate.

## One command

You need an account (a free one works and needs no card:
https://app.syntheticcfo.com/?signup=1) and an API key from the app (menu, your
email, Account, API access). Then:

```bash
syntheticcfo generate --key scfo_YOURKEY --platform oracle --industry retail --fraud high --rows 600
```

It builds the world, waits, and saves the package zip where you stand.
`python -m syntheticcfo` does the same thing everywhere.

| Command | What it does |
|---|---|
| `syntheticcfo generate` | builds a world, waits, and saves the package zip |
| `syntheticcfo download JOB_ID` | waits for a job if it is still building, then saves its package |
| `syntheticcfo reproduce JOB_ID` | runs a job again with its seed and configuration, then saves the new package |
| `syntheticcfo score JOB_ID FILE` | grades your detector's flagged record ids (a file, or `-` for standard input) against the job's answer key |
| `syntheticcfo me` | shows the account the key belongs to: plan, this month's generations, limits |
| `syntheticcfo jobs` | lists your recent generations, with the engine each ran on and what Reproduce would do |

Every command takes `--key` (or reads `SCFO_API_KEY`); `generate`, `download`
and `reproduce` also take `--lab` and `--out`. `generate` also takes:

| Option | What it sets |
|---|---|
| `--platform` | SAP_ECC (the default), SAP_S4HANA or ORACLE, plain words like oracle work; or a combined environment, SAP_MIGRATION_PAIR or INTERCOMPANY_GROUP, on the Team plan and above |
| `--modules` | the cycles, any of P2P O2C CE R2R HR MM FA (default: the core four, P2P O2C CE R2R) |
| `--industry` | TMT (the default), PHARMA, MANUFACTURING, FINANCIAL_SERVICES or RETAIL |
| `--fraud` | none, low, medium (the default) or high |
| `--rows` | the scale: base transactions per cycle, the setting the plans call rows (not a count of the rows the tables end up holding), up to your plan's limit (`syntheticcfo me` shows it). A value outside the platform's range is first brought into it; a value then above your plan's limit is refused with a message naming the limit. Left out, the world takes the standard volume; when your plan allows less, as the free plan does, it is sized at your plan's limit instead and the command prints the platform's note saying so |
| `--jurisdiction` | a country code such as US, DE, ZA or JP (`syntheticcfo generate --help` lists them) |
| `--seed` | any integer; the same seed and configuration on the same engine version rebuild the world byte for byte (on another engine version they can build a different dataset). Left out, a fresh one is drawn and printed at the end |
| `--years` | 1 for a single year; 2 to 5 for a multi-year arc on the Team plan and above (3 at most on the intercompany group; the migration pair is one year) |
| `--evaluation-mode` | withholds the ground-truth marker columns and moves the annotations out of the shipped data |
| `--lab` | also saves the JSON-lines lab export: one file per table, the answer key and the dataset card |
| `--out` | the folder or file path for the downloads (default: where you stand) |

## From Python

```python
from syntheticcfo import Client

c = Client(api_key="scfo_...")            # or set SCFO_API_KEY
job = c.generate(
    platform="SAP_ECC",                   # SAP_ECC | SAP_S4HANA | ORACLE
    industry="MANUFACTURING",
    fraud="medium",                       # none | low | medium | high
    rows=600,                             # the scale, up to your plan's limit: c.me()["caps"]["max_rows"]
    modules=["P2P", "O2C", "CE", "R2R"],  # subset of P2P O2C CE R2R HR MM FA
    jurisdiction="DE",
    seed=42,
    wait=True,
)
package = job.download("out/")            # the four-layer package (zip)
lab = job.lab_export("out/")              # JSONL tables + answer_key.jsonl + dataset card
print(job.insights())                     # what was planted, by control

flagged = my_detector(lab)                # your detector's record ids, as the tables spell them
result = job.score(flagged)               # graded against this job's answer key
print(result["precision"], result["recall"], result["f1"], result["grade"])

print(job.reproduce_note)                 # what Reproduce will do, before you call it
again = job.reproduce(wait=True)          # the same seed and configuration, as a new job
```

A flag is a record id as the answer key spells it: a document number, or a
composite such as `4500000123/10` for a purchase-order line, which also matches
on the purchase order alone and never on the line number alone. On a multi-year
arc, `FY2024:4500000123` grades a flag inside that year; a bare id counts in
every year that labels it. `job.score(text=...)` and `syntheticcfo score` also
take ids separated by newlines, commas or spaces, or a CSV with a `record_id`
column (a file of that one column works too: the client leaves its header out
of what it sends).

On the engine version a job ran on, Reproduce uses no generation and, for a
job that completed, rebuilds the same files. On another engine, or when the
platform has no record of the job's engine, the same seed and configuration
build a new dataset (its certificate names the engine), which counts as one
generation on a metered plan; the platform's note on the new job says which.
A job from before the platform stored configurations cannot be reproduced; the
platform refuses it and says why.

[examples/quickstart.py](examples/quickstart.py) generates a world and joins
the answer key onto a table with pandas.

## What you get

Every dataset you generate is a four-layer package: an executive brief with the whitepaper,
an audit evidence layer with the forensic report and the ground-truth answer
key (a blind-track dataset keeps part of the key sealed on the platform for
scoring), a SQLite database in which every key between the tables it ships
resolves, measured and printed in the package (references to masters of
modules you did not choose are listed there too; the database itself declares
no foreign keys), and source workbooks mirroring the real ERP table
architecture. A dataset with purchase to pay, order to cash, payroll,
inventory or fixed assets also carries articulated financial statements in the
executive brief. Seven cycles (purchase to pay, order to cash, cash and bank,
record to report, payroll, inventory, fixed assets), ten jurisdictions, and 37
labelled fraud schemes in the registry (35 across the seven cycles, 2 in the
intercompany group), each world planting those its cycles, platform and
settings allow, nearly every one beside innocent look-alikes so a detector can
be measurably wrong. The three closed sets by design are segregation of
duties, the ghost vendor and phantom inventory. The join or the
reconciliation that finds them is the real analytic, so every match is a
genuine exception. No look-alike is planted for the first two, and beside
phantom inventory an Oracle world carries stock that did not move, which the
roll-forward separates (https://app.syntheticcfo.com/proof).

## MCP server

`mcp_server/` lets Claude, Cursor, VS Code or any MCP client generate datasets,
download packages and read answer keys in plain language. Three-step setup in
[mcp_server/README.md](mcp_server/README.md).

It generates data in the shape of SAP ECC, SAP S/4HANA and Oracle Cloud
(`SAP_ECC`, `SAP_S4HANA` or `ORACLE` as the `platform`), plus the SAP
migration pair and the intercompany group on the Team plan and above.

To run it in a container over stdio, build the image from the Dockerfile at
the root of this repository and pass your key through the environment

```bash
docker build -t syntheticcfo-mcp .
docker run --rm -i -e SCFO_API_KEY syntheticcfo-mcp
```

Without a key the server still starts and lists its six tools, and the tools
that generate answer with an error asking for `SCFO_API_KEY`.

## Plans

The free Developer plan needs no request and no card: all three platforms and
the core four cycles, with the answer key, within the free plan's limits on
scale and on datasets a month, which https://app.syntheticcfo.com/evaluate
states. Start at https://app.syntheticcfo.com/?signup=1. Paid plans add the
remaining modules, a larger scale and unlimited generation; Team adds
multi-year arcs, the blind evaluation track and the two combined environments
(the SAP migration pair and the intercompany group), and Enterprise the hosted
ERP environment.

Plan limits apply exactly as in the app. A request for a module, platform or
feature your plan does not include is refused with a message naming what is
missing (an HTTP 402). A scale outside the platform's range is first brought
into it, without a note; a scale then above your plan's limit is refused with
a message naming the limit. A request with no row count takes the volume
tier's size (the standard tier's unless one is named); when that size is more
than your plan allows, as the standard tier's is on the free plan, it is sized
at your plan's limit instead and the platform says so.

## Licence

The code in this repository (the client, the command line and the MCP server)
is MIT licensed; see LICENSE. The platform, the generated data and their terms
of use are separate: https://app.syntheticcfo.com/terms
