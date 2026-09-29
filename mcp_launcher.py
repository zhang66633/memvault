"""Launch MemVault MCP stdio server via a single absolute path.

MCP clients differ in how they split the "args" field (some pass it as one
argument instead of splitting on spaces). This launcher keeps the client
config to a single argument and fixes the package path so the server starts
regardless of the client's working directory.
"""
import sys

sys.path.insert(0, r"D:\Claude_code\memory")

from memvault.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
