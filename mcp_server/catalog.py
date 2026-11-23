"""
synthetic cfo MCP server, the tool catalog.

One description of every tool, read by both transports: the stdio server
(server.py, which a client launches on the user's machine) and the hosted
connector at https://app.syntheticcfo.com/mcp (Streamable HTTP, signed in with
the user's own account). Plain data and the standard library only, so the
public copy carries it unchanged and nothing here imports the MCP SDK.

Each tool has a title and all three behaviour hints set explicitly
(readOnlyHint, destructiveHint, openWorldHint), plus idempotentHint, and an
output schema. The two transports differ only in how the read tools name a
package: the stdio server reads a folder on the user's machine (package_dir),
the hosted connector reads one of the signed-in account's own jobs (job_id).
"""

SERVER_NAME = "synthetic-cfo"
SERVER_TITLE = "synthetic cfo"
SERVER_VERSION = "1.2.0"
WEBSITE_URL = "https://syntheticcfo.com"
ICON_BASE = "https://app.syntheticcfo.com/static/"
ICONS = [{"src": ICON_BASE + "icon-%d.png" % s, "mimeType": "image/png", "sizes": ["%dx%d" % (s, s)]}
         for s in (48, 128, 256, 512)]
INSTRUCTIONS = (
    "synthetic cfo generates synthetic ERP datasets (SAP ECC, SAP S/4HANA, Oracle Cloud) from "
    "accounting rules, with a ground-truth answer key of the fraud planted in them. Generation is "
    "deterministic Python; no language model produces any financial value. list_capabilities "
    "shows the accepted values and the account's plan limits. Generating uses the account's "
    "plan, and on the free plan each dataset counts against the monthly allowance.")

# ---- generate_dataset's arguments (JSON Schema) ----
_GEN_PROPS = {
    "platform": {"type": "string",
                 "enum": ["SAP_ECC", "SAP_S4HANA", "ORACLE", "SAP_MIGRATION_PAIR", "INTERCOMPANY_GROUP"],
                 "description": ("Which ERP to emulate. Default SAP_ECC. SAP_MIGRATION_PAIR and "
                                 "INTERCOMPANY_GROUP are the two combined environments (Team plan and above).")},
    "modules": {"type": "array", "items": {"type": "string",
                "enum": ["P2P", "O2C", "CE", "R2R", "HR", "MM", "FA"]},
                "description": "Which business cycles to generate. Default the core four (P2P, O2C, CE, R2R)."},
    "industry": {"type": "string",
                 "enum": ["TMT", "PHARMA", "MANUFACTURING", "FINANCIAL_SERVICES", "RETAIL"]},
    "jurisdiction": {"type": "string",
                     "description": ("Country code, one of those list_capabilities returns (e.g. US, DE, ZA, GB, JP, IN). "
                                     "Sets currency, framework and tax regime.")},
    "region": {"type": "string", "enum": ["AMERICAS", "APAC", "EMEA", "GLOBAL"],
               "description": ("A multi-country region instead of one jurisdiction: the vendors and customers "
                               "follow the region's mix of countries. A jurisdiction given as well wins, and "
                               "the region is cleared.")},
    "fraud_intensity": {"type": "string", "enum": ["none", "low", "medium", "high"],
                        "description": "How much labelled fraud to inject. 'none' is a clean baseline."},
    "num_rows": {"type": "integer",
                 "description": ("The scale: base transactions per cycle, the setting the plans call rows (not a "
                                 "count of the rows the tables end up holding). Up to your plan's limit and the "
                                 "platform's ceiling, which list_capabilities shows; a value outside the platform's "
                                 "range is first brought into it, and one then above your plan's limit is refused. "
                                 "Omit it to take the volume tier's size, or your plan's limit when the plan allows "
                                 "less.")},
    "volume_tier": {"type": "string", "enum": ["proof_of_concept", "standard", "professional", "enterprise"],
                    "description": "The preset scale used when num_rows is omitted. Default standard."},
    "size_band": {"type": "string", "enum": ["startup", "smb", "mid_market", "enterprise", "global"],
                  "description": ("The company's size band, which sets its headcount and the size of its "
                                  "vendor and customer masters. Omit it to have the size inferred from the "
                                  "scale.")},
    "fiscal_year": {"type": "integer", "description": "Fiscal year, 2023 to 2028. Default 2025."},
    "num_years": {"type": "integer",
                  "description": ("1 for a single year; 2 to 5 for a multi-year fraud arc of consecutive years "
                                  "(Team plan and above; 3 at most on the intercompany group, one year on the "
                                  "migration pair).")},
    "seed": {"type": "integer",
             "description": ("Fix the seed: the same seed and configuration on the same engine version give "
                             "identical tables, rows and answer key (on another engine version they can build "
                             "a different dataset). Omit it for a fresh world.")},
    "company_code": {"type": "string",
                     "description": ("The SAP company code on every document: one to four letters or digits. "
                                     "Default the platform's own. The intercompany group keeps its "
                                     "subsidiaries' own codes.")},
    "evaluation_mode": {"type": "boolean",
                        "description": ("Withhold the ground-truth marker columns from the shipped database and "
                                        "workbooks and move the annotation layer out, so no column names a scheme "
                                        "on its own rows (the answer key still ships). On when omitted, the platform's "
                                        "default since engine 1.15.1; false asks for the annotated form, with the "
                                        "annotations and the marker columns on the rows.")},
    "blind_track": {"type": "integer", "enum": [0, 1],
                    "description": ("1 adds the blind evaluation track (Team plan and above): extra planted "
                                    "schemes whose answer key stays sealed outside the package, for the "
                                    "platform's scoring. evaluation_mode stays on with it unless that is given as "
                                    "false. Default 0.")},
    "entropy_level": {"type": "integer", "enum": [0, 1, 2, 3],
                      "description": ("Operational noise from 0 (none, the default) to 3: legitimate mess such as "
                                      "late reversals and duplicate vendor masters, labelled apart from the fraud "
                                      "answer key.")},
    "foreign_currency_share": {"type": "number", "minimum": 0, "maximum": 1,
                               "description": ("The share of vendors and customers abroad, invoicing in their own "
                                               "currency: a fraction from 0 (the default) to 1. A region already "
                                               "mixes currencies, so with a region it is cleared.")},
}


# the documents get_deliverable reads (mcp_server/tools.py DELIVERABLE_KINDS,
# kept equal by tools/connector_tests.py)
DELIVERABLE_KINDS = ("list", "brief", "audit_report", "answer_key_summary", "financial_statements",
                     "control_results", "tax_filing", "realism_report", "manifest", "certificate", "workbook")

# ---- what the read tools take to name a package ----
_PACKAGE_DIR = {"type": "string", "description": "The package_dir returned by generate_dataset."}
_JOB_ID = {"type": "string",
           "description": ("The job_id returned by generate_dataset or reproduce: one of the signed-in "
                           "account's own datasets.")}
_LEG = {"type": "string",
        "description": ("Optional: a leg of a multi-leg package (inspect_package lists them, e.g. "
                        "SAP_ECC / SAP_S4HANA on a migration pair).")}


def _hints(read_only, idempotent):
    return {"readOnlyHint": read_only, "destructiveHint": False,
            "idempotentHint": idempotent, "openWorldHint": False}


def _out(props, required=("ok",)):
    """A permissive output schema: every result is a JSON object carrying ok,
    an error result carries error, and the known fields are typed. Results
    may carry further fields (additionalProperties true)."""
    base = {"ok": {"type": "boolean"}, "error": {"type": "string"}}
    base.update(props)
    return {"type": "object", "properties": base, "required": list(required),
            "additionalProperties": True}


def _target(remote):
    """The read tools' package argument: job_id on the hosted connector,
    package_dir on the stdio server."""
    return ("job_id", _JOB_ID) if remote else ("package_dir", _PACKAGE_DIR)


def tool_definitions(remote=False):
    """Every tool as a plain dict in the shape of an MCP Tool (name, title,
    description, inputSchema, outputSchema, annotations). remote=True gives
    the hosted connector's form."""
    key, key_schema = _target(remote)
    where_files = ("a link that opens the dataset in the app, where it can be downloaded (a signed-out "
                   "visitor signs in first and comes back to it)" if remote
                   else "the folder the package was extracted to")
    gen_desc = (
        "Generate a synthetic ERP dataset from accounting rules alone (no real company data; no language "
        "model generates any financial value). The package has four layers: an executive brief, audit "
        "evidence including a ground-truth fraud answer key, a SQLite database, and source workbooks that "
        "mirror the real ERP tables. Uses the account's plan: on the free plan each dataset counts against "
        "the monthly allowance. Returns the job id, the seed, a summary of the package and %s; a large "
        "build that has not finished within the wait comes back with its job id and status running. The "
        "same seed and configuration on the same engine version give identical tables, rows and answer "
        "key." % where_files)
    defs = [
        {
            "name": "list_capabilities",
            "title": "List capabilities",
            "description": ("List the values generate_dataset accepts: ERP platforms and the combined "
                            "environments, business-cycle modules, industries, jurisdictions and regions, "
                            "fraud levels, volume tiers and size bands, and the limits of the signed-in "
                            "account's plan."),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": _out({"platforms": {"type": "array"}, "modules": {"type": "array"},
                                  "plan": {"type": ["string", "null"]}}, required=()),
            "annotations": _hints(True, True),
        },
        {
            "name": "generate_dataset",
            "title": "Generate a dataset",
            "description": gen_desc,
            "inputSchema": {"type": "object", "properties": _GEN_PROPS, "additionalProperties": False},
            "outputSchema": _out({"job_id": {"type": "string"}, "status": {"type": "string"},
                                  "seed": {"type": ["integer", "null"]},
                                  "download_url": {"type": "string"}, "package_dir": {"type": "string"},
                                  "summary": {"type": "object"}, "warnings": {"type": "array"}}),
            "annotations": _hints(False, False),
        },
        {
            "name": "inspect_package",
            "title": "Inspect a package",
            "description": ("Summarise a generated package: its shape (single world, multi-year arc, "
                            "intercompany group, group arc, migration pair) and legs, the database that "
                            "represents it with its tables and row counts, the fraud answer key broken down "
                            "by module, control and vector, the financial-statement headline and whether a "
                            "tax filing shipped." + (" A dataset still building reports its status and "
                                                     "progress." if remote else "")),
            "inputSchema": {"type": "object", "properties": {key: key_schema},
                            "required": [key], "additionalProperties": False},
            "outputSchema": _out({"status": {"type": "string"}, "shape": {"type": "string"},
                                  "tables": {"type": "object"}, "fraud_answer_key": {"type": "object"}}),
            "annotations": _hints(True, True),
        },
        {
            "name": "query_answer_key",
            "title": "Read the answer key",
            "description": ("Return the ground-truth fraud labels of a package as rows, for building or "
                            "scoring a fraud detector. Each row names the module, control id, fraud vector, "
                            "source table and the record id of one planted fraud. Optionally filtered by "
                            "module or control_id."),
            "inputSchema": {"type": "object", "properties": {
                key: key_schema,
                "module": {"type": "string", "description": "Optional filter, e.g. P2P, O2C, HR, MM, FA."},
                "control_id": {"type": "string", "description": "Optional filter, e.g. C-01, C-TAX-01."},
                "limit": {"type": "integer", "description": "Max rows to return (default 200, max 2000)."},
                "leg": dict(_LEG, description=_LEG["description"] + (
                    " Reads that leg's own key; a pair returns both legs' rows, tagged with their leg, "
                    "when no leg is named."))},
                "required": [key], "additionalProperties": False},
            "outputSchema": _out({"total_matching": {"type": "integer"}, "returned": {"type": "integer"},
                                  "labels": {"type": "array", "items": {"type": "object"}},
                                  "columns": {"type": "array"}}),
            "annotations": _hints(True, True),
        },
        {
            "name": "read_table",
            "title": "Read a table",
            "description": ("Read rows from a named table in a generated package's database. Supports an "
                            "optional single-column equality filter via where: "
                            "{\"column\": \"...\", \"equals\": \"...\"}."),
            "inputSchema": {"type": "object", "properties": {
                key: key_schema,
                "table": {"type": "string", "description": "Table name (inspect_package lists them)."},
                "limit": {"type": "integer", "description": "Max rows (default 50, max 500)."},
                "where": {"type": "object", "properties": {
                    "column": {"type": "string"}, "equals": {}}, "additionalProperties": False},
                "leg": dict(_LEG, description=_LEG["description"] + (
                    " Reads that leg's own database instead of the package's representative one."))},
                "required": [key, "table"], "additionalProperties": False},
            "outputSchema": _out({"table": {"type": "string"}, "total_rows": {"type": "integer"},
                                  "returned": {"type": "integer"},
                                  "rows": {"type": "array", "items": {"type": "object"}}}),
            "annotations": _hints(True, True),
        },
        {
            "name": "reproduce",
            "title": "Reproduce a dataset",
            "description": ("Build a package again and report the engine version and the SHA-256 file "
                            "hashes from its certificate. With the job_id of an earlier dataset it uses the "
                            "platform's Reproduce: free on the engine version that built that job, and on "
                            "another engine version it counts as one generation on a metered plan. With a "
                            "config and seed it is a new generation under the plan. The same seed and "
                            "configuration on the same engine version give identical tables, rows and "
                            "answer key; on another engine version they can build a different dataset."),
            "inputSchema": {"type": "object", "properties": {
                "job_id": {"type": "string", "description": "The job_id of an earlier dataset."},
                "config": {"type": "object", "description": "The config object to reproduce."},
                "seed": {"type": "integer"}},
                "additionalProperties": False},
            "outputSchema": _out({"job_id": {"type": "string"}, "status": {"type": "string"},
                                  "engine_version": {"type": "string"},
                                  "file_hashes": {"type": ["array", "null"]}}),
            "annotations": _hints(False, False),
        },
        {
            "name": "get_deliverable",
            "title": "Read a deliverable",
            "description": ("Return one of a generated package's documents for reading in the chat: the executive "
                            "brief (the whitepaper with the reproducibility certificate), the forensic audit report, "
                            "the financial statements with their workpaper, the control results, the answer-key "
                            "summary, the tax filing, the realism report, a module manifest, or one sheet of a "
                            "source workbook. PDFs come back as their text and tables as rows, capped to a chat-sized "
                            "part with an offset for the rest. kind 'list' (the default) names the documents this "
                            "package holds."),
            "inputSchema": {"type": "object", "properties": {
                key: key_schema,
                "kind": {"type": "string", "enum": list(DELIVERABLE_KINDS),
                         "description": "Which document. Default list."},
                "workbook": {"type": "string",
                             "description": "For kind workbook: the file name, as kind list names it."},
                "sheet": {"type": "string", "description": "For kind workbook: the sheet name."},
                "module": {"type": "string", "description": "For kind manifest: the module, e.g. P2P."},
                "limit": {"type": "integer",
                          "description": "Rows of a table (default 50, max 200)."},
                "offset": {"type": "integer",
                           "description": ("Where to start: a row for a table, a character for text "
                                           "(from next_offset).")},
                "leg": dict(_LEG, description=_LEG["description"] + (
                    " Most documents of a multi-leg package sit in each leg."))},
                "required": [key], "additionalProperties": False},
            "outputSchema": _out({"kind": {"type": "string"}, "title": {"type": "string"},
                                  "format": {"type": "string"}, "text": {"type": "string"},
                                  "columns": {"type": "array"}, "rows": {"type": "array"},
                                  "documents": {"type": "array", "items": {"type": "object"}},
                                  "open_in_app": {"type": "string"}}),
            "annotations": _hints(True, True),
        },
    ]
    for d in defs:
        d["annotations"] = dict(d["annotations"], title=d["title"])
    return defs


TOOL_NAMES = tuple(d["name"] for d in tool_definitions())
