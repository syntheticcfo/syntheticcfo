"""
synthetic cfo MCP server, transport layer.

Exposes the tools in tools.py over the Model Context Protocol (stdio), so any
MCP client (Claude, Cursor, VS Code, and the rest) can generate forensic-grade
synthetic ERP data, read it, and grade a detector against its ground-truth fraud
answer key, without leaving the tool the user is working in.

Run:  python /path/to/mcp_server/server.py   (stdio; an MCP client launches it,
      from whatever folder it starts in: the full path is what lets it find
      its package, where "python -m mcp_server" works only from the folder
      that holds mcp_server/)
"""
import os
import sys
import json
import asyncio

# ensure the repo root is importable when launched as a script from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.lowlevel import Server, NotificationOptions          # noqa: E402
from mcp.server.models import InitializationOptions                  # noqa: E402
import mcp.types as T                                                # noqa: E402

from mcp_server import tools                                         # noqa: E402

SERVER_NAME = "synthetic-cfo"
SERVER_VERSION = "1.0.0"

# ---- tool schemas (JSON Schema for each tool's arguments) ----
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
                     "description": ("Country code (list_capabilities lists them, e.g. US, DE, ZA, GB, JP, IN). "
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
                             "byte-identical files (on another engine version they can build a different "
                             "dataset). Omit it for a fresh world.")},
    "company_code": {"type": "string",
                     "description": ("The SAP company code on every document: one to four letters or digits. "
                                     "Default the platform's own. The intercompany group keeps its "
                                     "subsidiaries' own codes.")},
    "evaluation_mode": {"type": "boolean",
                        "description": ("Withhold the ground-truth marker columns from the shipped database and "
                                        "workbooks and move the annotation layer out, so no column names a scheme "
                                        "on its own rows (the answer key still ships). Omitted, it is on with the "
                                        "blind track and off otherwise.")},
    "blind_track": {"type": "integer", "enum": [0, 1],
                    "description": ("1 adds the blind evaluation track (Team plan and above): extra planted "
                                    "schemes whose answer key stays sealed outside the package, for the "
                                    "platform's scoring. It switches evaluation_mode on unless that is given as "
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

TOOLS = [
    T.Tool(
        name="list_capabilities",
        description=("List the values generate_dataset takes: ERP platforms and the combined "
                     "environments, business-cycle modules, industries, jurisdictions and regions, "
                     "fraud levels, volume tiers and size bands, and the scale limits that apply. "
                     "Call this first if unsure what to request."),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    T.Tool(
        name="generate_dataset",
        description=("Generate a forensic-grade synthetic ERP dataset from accounting "
                     "rules alone (no real data, no LLM in the financial generation). "
                     "Produces a four-layer package: an executive brief, audit evidence "
                     "including a ground-truth fraud answer key, a SQLite database in "
                     "which every key between the tables it ships resolves (measured and "
                     "printed in the package, where references to masters of modules not "
                     "chosen are listed too), and source workbooks mirroring the real "
                     "ERP tables. Returns the package location and a summary, with the "
                     "platform's notes under warnings. The same seed and configuration on "
                     "the same engine version regenerate a byte-identical package (its "
                     "certificate names the engine; on another engine version the same "
                     "seed can build a different dataset)."),
        inputSchema={"type": "object", "properties": _GEN_PROPS, "additionalProperties": False},
    ),
    T.Tool(
        name="inspect_package",
        description=("Summarise a generated package: its shape (single world, multi-year arc, "
                     "intercompany group, group arc, migration pair) and legs, the ONE database "
                     "that represents it with its tables and row counts, the fraud answer-key "
                     "breakdown by module / control / vector (and by entity, year or leg where "
                     "the key carries them), the financial-statement headline and whether a tax "
                     "filing shipped. The package facts the documents were written from come "
                     "back only when the engine is beside the server, which it is not in the "
                     "public copies (facts_source says so)."),
        inputSchema={"type": "object", "properties": {
            "package_dir": {"type": "string", "description": "The package_dir returned by generate_dataset."}},
            "required": ["package_dir"], "additionalProperties": False},
    ),
    T.Tool(
        name="query_answer_key",
        description=("Return the ground-truth fraud labels as structured rows, so you can "
                     "build or SCORE a fraud detector against the truth. Each row names the "
                     "module, control id, fraud vector, source table and the exact record "
                     "id of one injected fraud. Optionally filter by module or control_id."),
        inputSchema={"type": "object", "properties": {
            "package_dir": {"type": "string"},
            "module": {"type": "string", "description": "Optional filter, e.g. P2P, O2C, HR, MM, FA."},
            "control_id": {"type": "string", "description": "Optional filter, e.g. C-01, C-TAX-01."},
            "limit": {"type": "integer", "description": "Max rows to return (default 200, max 2000)."},
            "leg": {"type": "string", "description": ("Optional: a leg of a multi-leg package (inspect_package "
                                                      "lists them, e.g. SAP_ECC / SAP_S4HANA on a migration pair) "
                                                      "to read that leg's own key. A pair returns both legs' rows, "
                                                      "tagged with their leg, when no leg is named.")}},
            "required": ["package_dir"], "additionalProperties": False},
    ),
    T.Tool(
        name="read_table",
        description=("Read rows from a named table in the generated database, so you can "
                     "use the data inline. Supports an optional single-column equality "
                     "filter via `where`: {\"column\": \"...\", \"equals\": \"...\"}."),
        inputSchema={"type": "object", "properties": {
            "package_dir": {"type": "string"},
            "table": {"type": "string", "description": "Table name (see inspect_package for the list)."},
            "limit": {"type": "integer", "description": "Max rows (default 50, max 500)."},
            "where": {"type": "object", "properties": {
                "column": {"type": "string"}, "equals": {}}, "additionalProperties": False},
            "leg": {"type": "string", "description": ("Optional: a leg of a multi-leg package (inspect_package "
                                                      "lists them) to read that leg's own database instead of "
                                                      "the package's representative one.")}},
            "required": ["package_dir", "table"], "additionalProperties": False},
    ),
    T.Tool(
        name="reproduce",
        description=("Build a package again and report the engine version and the SHA-256 "
                     "file hashes from its certificate, so you can check reproduction: the "
                     "same seed and configuration on the same engine version give the same "
                     "hashes, and on another engine version they can build a different "
                     "dataset. Pass the `job_id` of an earlier generate_dataset result to use "
                     "the platform's Reproduce (free on the engine version that built that "
                     "job; on another engine it counts as one generation on a metered plan), "
                     "or the `config` and `seed` from its `reproduce` field for a new "
                     "generation under your plan."),
        inputSchema={"type": "object", "properties": {
            "job_id": {"type": "string",
                       "description": "The job_id of an earlier generate_dataset result (hosted mode)."},
            "config": {"type": "object", "description": "The config object to reproduce."},
            "seed": {"type": "integer"}},
            "additionalProperties": False},
    ),
]

_DISPATCH = {
    "list_capabilities": lambda a: tools.list_capabilities(),
    "generate_dataset": lambda a: tools.generate_dataset(a),
    "inspect_package": lambda a: tools.inspect_package(a.get("package_dir")),
    "query_answer_key": lambda a: tools.query_answer_key(
        a.get("package_dir"), a.get("module"), a.get("control_id"), a.get("limit", 200), a.get("leg")),
    "read_table": lambda a: tools.read_table(
        a.get("package_dir"), a.get("table"), a.get("limit", 50), a.get("where"), a.get("leg")),
    "reproduce": lambda a: tools.reproduce(a.get("config"), a.get("seed"), a.get("job_id")),
}


def build_server():
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools():
        return TOOLS

    @server.call_tool()
    async def _call_tool(name, arguments):
        fn = _DISPATCH.get(name)
        if fn is None:
            payload = {"ok": False, "error": "unknown tool %r" % name}
        else:
            # generation is CPU-heavy and blocking; run it off the event loop so
            # the stdio transport stays responsive.
            try:
                payload = await asyncio.to_thread(fn, arguments or {})
            except Exception as e:
                payload = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return [T.TextContent(type="text", text=json.dumps(payload, default=str, indent=2))]

    return server


async def _run(mcp_stdout):
    import anyio
    from io import TextIOWrapper
    from mcp.server.stdio import stdio_server
    server = build_server()
    out = anyio.wrap_file(TextIOWrapper(mcp_stdout, encoding="utf-8"))
    async with stdio_server(stdout=out) as (read, write):
        await server.run(
            read, write,
            InitializationOptions(
                server_name=SERVER_NAME, server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={}),
            ),
        )


def main():
    # The stdio transport owns real stdout for JSON-RPC. The deterministic engine
    # and its libraries print progress to stdout, which would corrupt that stream,
    # so we hand the transport the REAL stdout and redirect the Python-level
    # sys.stdout to stderr - every engine print then lands on stderr, harmlessly.
    import io
    real_stdout = sys.stdout.buffer
    sys.stdout = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    asyncio.run(_run(real_stdout))


if __name__ == "__main__":
    main()
