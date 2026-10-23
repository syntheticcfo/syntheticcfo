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
from .server import build_server, main

__all__ = ["build_server", "main"]
