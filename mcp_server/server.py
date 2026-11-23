"""
synthetic cfo MCP server, transport layer.

Exposes the tools in tools.py over the Model Context Protocol (stdio; the
hosted connector at https://app.syntheticcfo.com/mcp serves the same catalog
over Streamable HTTP), so any MCP client (Claude, Cursor, VS Code, and the
rest) can generate forensic-grade synthetic ERP data, read it, and grade a detector against its ground-truth fraud
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
import mcp.types as T                                                # noqa: E402

from mcp_server import tools                                         # noqa: E402

from mcp_server import catalog                                       # noqa: E402

SERVER_NAME = catalog.SERVER_NAME
SERVER_VERSION = catalog.SERVER_VERSION

# The tools, their titles, behaviour hints and output schemas live in
# catalog.py, which the hosted connector at app.syntheticcfo.com/mcp reads too.
_GEN_PROPS = catalog._GEN_PROPS
TOOLS = [T.Tool(**d) for d in catalog.tool_definitions(remote=False)]

_DISPATCH = {
    "list_capabilities": lambda a: tools.list_capabilities(),
    "generate_dataset": lambda a: tools.generate_dataset(a),
    "inspect_package": lambda a: tools.inspect_package(a.get("package_dir")),
    "query_answer_key": lambda a: tools.query_answer_key(
        a.get("package_dir"), a.get("module"), a.get("control_id"), a.get("limit", 200), a.get("leg")),
    "read_table": lambda a: tools.read_table(
        a.get("package_dir"), a.get("table"), a.get("limit", 50), a.get("where"), a.get("leg")),
    "reproduce": lambda a: tools.reproduce(a.get("config"), a.get("seed"), a.get("job_id")),
    "get_deliverable": lambda a: tools.get_deliverable(
        a.get("package_dir"), a.get("kind"), a.get("workbook"), a.get("sheet"), a.get("module"),
        a.get("limit"), a.get("offset"), a.get("leg")),
}


def _local_paths(payload, pkg):
    """On this machine the package is local, so each file a deliverable names
    is given as its full path (the hosted connector gives a download link)."""
    groups = [payload.get("files")] + [d.get("files") for d in payload.get("documents") or []
                                       if isinstance(d, dict)]
    for files in groups:
        for f in files or []:
            if isinstance(f, dict) and f.get("path") and pkg:
                f["local_path"] = os.path.join(pkg, *f["path"].split("/"))


def _icons():
    try:
        return [T.Icon(**i) for i in catalog.ICONS]
    except Exception:
        return None


def build_server():
    server = Server(SERVER_NAME, version=SERVER_VERSION, instructions=catalog.INSTRUCTIONS,
                    website_url=catalog.WEBSITE_URL, icons=_icons())

    @server.list_tools()
    async def _list_tools():
        return TOOLS

    @server.call_tool(validate_input=False)
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
        if not isinstance(payload, dict):
            payload = {"ok": True, "result": payload}
        payload = json.loads(json.dumps(payload, default=str))
        structured = payload
        if name == "get_deliverable":
            # the document's words once, in the readable block; the structured
            # result carries the rest (title, paging, the files on disk)
            _local_paths(payload, (arguments or {}).get("package_dir") or "")
            content = [T.TextContent(type="text", text=tools.deliverable_text(payload))]
            structured = tools.deliverable_meta(payload)
        else:
            content = [T.TextContent(type="text", text=json.dumps(payload, indent=2))]
        return T.CallToolResult(content=content, structuredContent=structured,
                                isError=payload.get("ok") is False)

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
            server.create_initialization_options(NotificationOptions(), {}),
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
