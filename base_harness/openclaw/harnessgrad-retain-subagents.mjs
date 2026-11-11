export default {
  id: "harnessgrad-retain-subagents",
  register(api) {
    api.on("before_tool_call", ({ toolName, params }) => {
      if (toolName === "sessions_spawn" && params.cleanup === "delete") {
        return { params: { ...params, cleanup: "keep" } };
      }
      if (toolName !== "dispatch_action" && toolName !== "tool_call") return;
      if (typeof params.id === "string" && params.id.split(":").at(-1) === "sessions_spawn"
          && params.args?.cleanup === "delete") {
        return { params: { ...params, args: { ...params.args, cleanup: "keep" } } };
      }
    });
  },
};
