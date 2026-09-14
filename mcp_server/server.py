import os
from mcp.server.fastmcp import FastMCP

# Import tool implementations from each domain module
from mcp_server.tools.postgres import search_tasks, get_task_by_id, create_task, update_task_status, list_projects
from mcp_server.tools.jira import sync_task_to_jira
from mcp_server.tools.calendar import check_owner_availability
from mcp_server.tools.report import generate_report

# Read port from env so it can be overridden in different environments
port = int(os.getenv("MCP_SERVER_PORT", "8001"))

# Create the FastMCP server instance.
# host/port are set here on the constructor, NOT on run() - as required by mcp v1.27+
mcp = FastMCP("task-planner", host="0.0.0.0", port=port)

# Register each function as an MCP tool.
# The agent connects to this server via SSE and discovers these tools at runtime.
mcp.tool()(search_tasks)
mcp.tool()(get_task_by_id)
mcp.tool()(create_task)
mcp.tool()(update_task_status)
mcp.tool()(list_projects)
mcp.tool()(sync_task_to_jira)
mcp.tool()(check_owner_availability)
mcp.tool()(generate_report)

if __name__ == "__main__":
    # Start the server with SSE transport (HTTP + Server-Sent Events).
    # Run via: python3 -m mcp_server.server
    mcp.run(transport="sse")
