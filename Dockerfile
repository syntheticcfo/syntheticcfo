# synthetic cfo MCP server on stdio, for Glama and any container runtime
# Build   docker build -t syntheticcfo-mcp .
# Run     docker run --rm -i -e SCFO_API_KEY syntheticcfo-mcp
# The server starts and lists its six tools without a key. The tools that
# generate or reproduce answer with an error asking for SCFO_API_KEY until it is set.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SCFO_MCP_OUTPUT_DIR=/tmp/synthetic_cfo_mcp

WORKDIR /app

COPY mcp_server/requirements.txt mcp_server/requirements.txt
RUN pip install -r mcp_server/requirements.txt

COPY pyproject.toml README.md LICENSE syntheticcfo.py ./
COPY mcp_server ./mcp_server
RUN pip install --no-deps . \
    && useradd --create-home --uid 10001 mcp

USER mcp

CMD ["python", "/app/mcp_server/server.py"]
