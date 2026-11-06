/* Presentation only: method IDs remain the server's identifiers. */
((root, factory) => {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.HGLaunchMethods = factory();
})(typeof window === "undefined" ? globalThis : window, () => {
  "use strict";
  const labels = Object.freeze({
    ahe: "AHE", dgm: "DGM", gepa: "GEPA", adas: "ADAS", ace: "ACE",
    skillopt: "SkillOpt", opro: "OPRO", meta_harness: "Meta-Harness",
    evo_harness: "Evo-Harness", evoprompt: "EvoPrompt", arbor: "Arbor",
    darwinx: "DarwinX", modular_rsi: "Modular RSI", dream_rsi: "Dream-RSI",
    harnessx: "HarnessX", hyperagents: "HyperAgents", lightrsi: "LightRSI",
    rrsi: "RRSI", sica_ci: "SICA", tthe: "TTHE",
    noop: "No-op", raise_step_cap: "Raise step cap", llm_improver: "LLM Improver"
  });
  const hidden = new Set(["a", "b", "echo_base"]);
  function rows(methods, noneLabel) {
    return [{ value: "none", label: noneLabel }, ...methods
      .filter((m) => !hidden.has(m.name))
      .map((m) => ({ value: m.name, label: Object.hasOwn(labels, m.name) ? labels[m.name] : m.name }))];
  }
  function selected(rows, current, fallback) {
    return [current, fallback, "none"].find((value) => rows.some((r) => r.value === value));
  }
  return { rows, selected };
});
