"""
synthetic cfo MCP server, transport layer.

Exposes the tools in tools.py over the Model Context Protocol (stdio), so any
MCP client (Claude, Cursor, VS Code, and the rest) can generate forensic-grade
synthetic ERP data, read it, and grade a detector against its ground-truth fraud
answer key, without leaving the tool the user is working in.

Run:  python -m mcp_server        (stdio; a client launches this)
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
                 "description": "Which ERP to emulate. Default SAP_ECC."},
    "modules": {"type": "array", "items": {"type": "string",
                "enum": ["P2P", "O2C", "CE", "R2R", "HR", "MM", "FA"]},
                "description": "Which business cycles to generate. Default the core four (P2P, O2C, CE, R2R)."},
    "industry": {"type": "string",
                 "enum": ["TMT", "PHARMA", "MANUFACTURING", "FINANCIAL_SERVICES", "RETAIL"]},
    "jurisdiction": {"type": "string",
                     "description": "Country code (e.g. US, DE, ZA, GB, JP, IN). Sets currency, framework and tax regime."},
    "fraud_intensity": {"type": "string", "enum": ["none", "low", "medium", "high"],
                        "description": "How much labelled fraud to inject. 'none' is a clean baseline."},
    "num_rows": {"type": "integer", "description": "Approximate transaction volume (100 to 1,000,000; Oracle caps at 200,000)."},
    "volume_tier": {"type": "string", "enum": ["proof_of_concept", "standard", "professional", "enterprise"],
                    "description": "Preset volume instead of num_rows."},
    "fiscal_year": {"type": "integer", "description": "Fiscal year, 2023 to 2028. Default 2025."},
    "num_years": {"type": "integer", "description": "Generate a multi-year fraud arc of this many consecutive years (1 to 5)."},
    "seed": {"type": "integer", "description": "Fix the seed for byte-identical reproduction. Omit for a fresh random world."},
}

TOOLS = [
    T.Tool(
        name="list_capabilities",
        description=("List everything you can ask generate_dataset for: ERP platforms, "
                     "business-cycle modules, industries, jurisdictions, fraud levels, "
                     "volume tiers and row limits. Call this first if unsure what to request."),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    T.Tool(
        name="generate_dataset",
        description=("Generate a forensic-grade synthetic ERP dataset from accounting "
                     "rules alone (no real data, no LLM in the financial generation). "
                     "Produces a four-layer package: an executive brief, audit evidence "
                     "including a ground-truth fraud answer key, a SQLite database with "
                     "full referential integrity, and source workbooks mirroring the real "
                     "ERP tables. Returns the package location and a summary. Same seed + "
                     "config regenerates a byte-identical package."),
        inputSchema={"type": "object", "properties": _GEN_PROPS, "additionalProperties": False},
    ),
    T.Tool(
        name="inspect_package",
        description=("Summarise a generated package: its database tables and row counts, "
                     "the fraud answer-key breakdown by module / control / vector, the "
                     "financial-statement headline, and whether a tax filing shipped."),
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
            "limit": {"type": "integer", "description": "Max rows to return (default 200, max 2000)."}},
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
                "column": {"type": "string"}, "equals": {}}, "additionalProperties": False}},
            "required": ["package_dir", "table"], "additionalProperties": False},
    ),
    T.Tool(
        name="reproduce",
        description=("Regenerate a package from an exact config + seed and report its "
                     "SHA-256 file hashes, so you can verify byte-identical reproduction, "
                     "the product's core guarantee. Pass the `config` and `seed` from a "
                     "previous generate_dataset result's `reproduce` field."),
        inputSchema={"type": "object", "properties": {
            "config": {"type": "object", "description": "The config object to reproduce."},
            "seed": {"type": "integer"}},
            "required": ["config", "seed"], "additionalProperties": False},
    ),
]

_DISPATCH = {
    "list_capabilities": lambda a: tools.list_capabilities(),
    "generate_dataset": lambda a: tools.generate_dataset(a),
    "inspect_package": lambda a: tools.inspect_package(a.get("package_dir")),
    "query_answer_key": lambda a: tools.query_answer_key(
        a.get("package_dir"), a.get("module"), a.get("control_id"), a.get("limit", 200)),
    "read_table": lambda a: tools.read_table(
        a.get("package_dir"), a.get("table"), a.get("limit", 50), a.get("where")),
    "reproduce": lambda a: tools.reproduce(a.get("config"), a.get("seed")),
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
