from mcp.server.fastmcp import FastMCP

mcp = FastMCP("toy-mcp")


@mcp.tool()
def read_file(path: str) -> str:
    """Read a local file and return its content as a string."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"file not found: {path}"
    except Exception as e:
        return f"error: {str(e)}"


if __name__ == "__main__":
    mcp.run()
