"""
synthetic cfo MCP server
========================
A Model Context Protocol server that brings the synthetic cfo cold start engine
into any MCP client (Claude, Cursor, VS Code and the rest), so a developer or an
agent can generate forensic-grade synthetic ERP data, and its labelled fraud
answer key, without leaving the tool they are working in.

It drives the SAME deterministic engine as the product (engine.orchestrator),
so a dataset generated here is byte-identical to the batch product for the same
seed and configuration. No language model ever generates the financial data; the
client only issues a validated request and the deterministic engine executes it.
"""
from .server import build_server, main

__all__ = ["build_server", "main"]
