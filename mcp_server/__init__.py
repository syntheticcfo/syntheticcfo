"""
synthetic cfo MCP server
========================
A Model Context Protocol server that brings the synthetic cfo cold start engine
into any MCP client (Claude, Cursor, VS Code and the rest), so a developer or an
agent can generate forensic-grade synthetic ERP data, and its labelled fraud
answer key, without leaving the tool they are working in.

It generates on app.syntheticcfo.com with the API key in SCFO_API_KEY, through
the same deterministic engine as the product, so the same seed and
configuration on the same engine version give a byte-identical dataset (on
another engine version they can build a different one). No language model ever
generates the financial data; the agent only issues a request and the engine
executes it.
"""
__all__ = ["build_server", "main"]


def __getattr__(name):
    # The stdio transport (server.py) needs the mcp package; the tool catalog
    # (catalog.py) and the tools (tools.py) do not. The hosted connector in the
    # app imports those two, and the app's image carries no mcp package, so the
    # transport loads only when something asks for it.
    if name in ("build_server", "main"):
        from . import server
        return getattr(server, name)
    raise AttributeError("module 'mcp_server' has no attribute %r" % name)
