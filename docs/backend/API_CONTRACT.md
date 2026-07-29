# Backend API Contract

Backend routes are domain-neutral in this base project. New product routes may be added by downstream projects, but they must follow the same boundary:

- require explicit valid `workspace_id` for workspace-scoped data;
- return stable JSON error shapes;
- avoid leaking absolute local paths;
- route tool execution through `ToolRuntimeClient`;
- keep long-running work in the durable runtime or job system.
