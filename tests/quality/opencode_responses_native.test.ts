import { describe, expect, test } from "bun:test"
import { createHash } from "node:crypto"
import { readFileSync } from "node:fs"
import path from "node:path"
import { pathToFileURL } from "node:url"
import { observeFetch } from "../../eval/service/responses_transport.mjs"

// This suite uses the frozen real parser and its mock HTTP layer. It never uses
// global fetch, resolves credentials, installs packages, or starts OpenCode/DSH.
const source = process.env.HG_TEST_OPENCODE_SOURCE
if (!source) {
  test.skip("native Responses replay requires explicit HG_TEST_OPENCODE_SOURCE", () => {})
} else {
  const root = path.resolve(source)
  const load = (relative: string) => import(pathToFileURL(path.join(root, relative)).href)
  const parserHashes = {
    "packages/ai/src/protocols/open-responses.ts": "85e9f03eccaaad1fe39d029c810f888b94a900dfc5f44de8c0f440a4977dcd29",
    "packages/ai/src/protocols/openai-responses.ts": "a08a3dc8a4187e0bee12de8af5390183342127c8eb35bc91dbb44304c597e248",
  }
  for (const [relative, expected] of Object.entries(parserHashes)) {
    const actual = createHash("sha256").update(readFileSync(path.join(root, relative))).digest("hex")
    if (actual !== expected) throw new Error(`Pinned OpenCode parser hash mismatch: ${relative}`)
  }
  const { Effect } = await load("packages/ai/node_modules/effect/dist/index.js")
  const { LLM, Message } = await load("packages/ai/src/index.ts")
  const { Auth, LLMClient } = await load("packages/ai/src/route.ts")
  const { route } = await load("packages/ai/src/protocols/openai-responses.ts")
  const { fixedResponse, dynamicResponse } = await load("packages/ai/test/lib/http.ts")
  const target = "https://offline.invalid/v1/responses"
  const model = route.with({ endpoint: { baseURL: "https://offline.invalid/v1/" }, auth: Auth.bearer("offline-placeholder") })
    .model({ id: "offline-native-parser-fixture" })
  const request = LLM.request({ model, prompt: "Offline parser regression only." })
  const usage = { input_tokens: 7, output_tokens: 5, total_tokens: 12,
    input_tokens_details: { cached_tokens: 2 }, output_tokens_details: { reasoning_tokens: 1 } }
  const completed = { type: "response.completed", response: { id: "resp_fixture", status: "completed", usage } }
  const added = (index = 0, id = "msg_fixture", type = "message") => ({
    type: "response.output_item.added", output_index: index, item: { id, type },
  })
  const delta = (itemId = true) => ({ type: "response.output_text.delta", output_index: 0,
    content_index: 0, delta: "Recovered text: \u4f60\u597d\u{1f642}", ...(itemId ? { item_id: "msg_fixture" } : {}) })
  const done = { type: "response.output_item.done", output_index: 0, item: { id: "msg_fixture", type: "message" } }
  const wire = (events: any[], newline = "\n") => events.map(event => `data: ${JSON.stringify(event)}${newline}${newline}`).join("")
  const textEvents = (itemId = true) => [added(), delta(itemId), done, completed]
  const sseHeaders = { "content-type": "text/event-stream", "x-request-id": "offline-server-request" }
  type Outcome = { ok: true, response: any } | { ok: false, error: any }

  function chunks(text: string, step = 7, terminalError?: Error) {
    const bytes = new TextEncoder().encode(text)
    let offset = 0
    return new ReadableStream<Uint8Array>({
      pull(controller) {
        if (offset < bytes.length) {
          controller.enqueue(bytes.slice(offset, offset + step))
          offset += Math.min(step, bytes.length - offset)
        } else if (terminalError) controller.error(terminalError)
        else controller.close()
      },
    })
  }

  async function parse(body: string | ReadableStream<Uint8Array>, selectedRequest = request): Promise<Outcome> {
    return Effect.runPromise(LLMClient.generate(selectedRequest).pipe(
      Effect.provide(fixedResponse(body)),
      Effect.match({ onSuccess: (response: any) => ({ ok: true, response }), onFailure: (error: any) => ({ ok: false, error }) }),
    ))
  }

  async function replay(text: string, mode: "observe" | "repair" = "repair", terminalError?: Error) {
    let fetchCalls = 0
    const records: any[] = []
    const fetch = observeFetch(async () => {
      fetchCalls++
      return new Response(chunks(text, 1, terminalError), { headers: sseHeaders })
    }, { route: target, mode, record: (record: any) => records.push(record) })
    const response = await fetch(target, { method: "POST", body: '{"model":"offline-native-parser-fixture","stream":true}' })
    const outcome = await parse(response.body!)
    expect(fetchCalls).toBe(1)
    return { outcome, records }
  }

  function success(outcome: Outcome) {
    if (!outcome.ok) throw new Error(`Expected native parser success: ${outcome.error.message}`)
    return outcome.response
  }

  function failure(outcome: Outcome) {
    expect(outcome.ok).toBe(false)
    if (outcome.ok) throw new Error("Unexpected native parser success")
    return outcome.error
  }

  describe("pinned native OpenCode Responses replay (mock HTTP only)", () => {
    test("authored synthetic fragment requires an active message mapping",async()=>{
      // Fictional regression input, authored here; this is not observed evidence.
      const fragment={type:"response.output_text.delta",response_id:"resp_synthetic_mapping",
        output_index:0,content_index:0,delta:"Fictional parser mapping example."}
      const message={id:"msg_synthetic_mapping",type:"message"}
      const finished={...completed,response:{...completed.response,id:"resp_synthetic_mapping"}}
      const synthetic=wire([added(0,message.id),fragment,
        {type:"response.output_item.done",output_index:0,item:message},finished])
      expect(failure(await parse(synthetic)).message).toContain("missing item_id")
      const {outcome,records}=await replay(synthetic)
      expect(success(outcome).text).toBe(fragment.delta)
      const repair=records.find(r=>r.repaired)
      expect(repair.original).toEqual(fragment)
      expect(repair.normalized).toEqual({...fragment,item_id:message.id})
      expect(repair.mapping_source).toBe("response.output_item.added")
      expect(failure((await replay(wire([fragment,finished]))).outcome).message)
        .toContain("no active message mapping")
    })
    test("compliant streams retain native text, events and usage in both modes", async () => {
      const text = wire(textEvents())
      const baseline = success(await parse(text))
      expect(baseline.text).toBe(delta().delta)
      expect(baseline.usage).toMatchObject({ inputTokens: 7, outputTokens: 5, totalTokens: 12, cacheReadInputTokens: 2, reasoningTokens: 1 })
      for (const mode of ["observe", "repair"] as const) {
        const { outcome, records } = await replay(text, mode)
        const response = success(outcome)
        expect(response.events).toEqual(baseline.events)
        expect(response.usage).toEqual(baseline.usage)
        expect(response.finishReason).toEqual(baseline.finishReason)
        expect(records.some(record => record.repaired)).toBe(false)
      }
    })

    test("the observed defect still fails strict parsing and repairs only the known slot", async () => {
      const missing = wire(textEvents(false))
      const nativeError = failure(await parse(missing))
      expect(nativeError.reason._tag).toBe("InvalidProviderOutput")
      expect(nativeError.message).toContain("missing item_id")
      expect(failure((await replay(missing, "observe")).outcome).message).toContain("missing item_id")
      const { outcome, records } = await replay(missing)
      const repaired = success(outcome), baseline = success(await parse(wire(textEvents())))
      expect(repaired.text).toBe(delta().delta)
      expect(repaired.events).toEqual(baseline.events)
      expect(repaired.usage).toEqual(baseline.usage)
      const repairs = records.filter(record => record.repaired)
      expect(repairs).toHaveLength(1)
      expect(repairs[0].original).not.toHaveProperty("item_id")
      expect(repairs[0].normalized.item_id).toBe("msg_fixture")
      expect(repairs[0].mapping_source).toBe("response.output_item.added")
    })

    test("orphan deltas fail without fabricated IDs and retain the rejected original", async () => {
      const text = wire([delta(false), completed])
      expect(failure(await parse(text)).message).toContain("missing item_id")
      const { outcome, records } = await replay(text)
      expect(failure(outcome).message).toContain("no active message mapping")
      expect(records.some(record => record.repaired)).toBe(false)
      const rejected = records.find(record => record.normalization_error)
      expect(rejected.original).toEqual(delta(false))
      expect(rejected.original).not.toHaveProperty("item_id")
    })

    for (const [name, events] of [
      ["text done", [added(), { type: "response.output_text.done", output_index: 0, text: "not repaired" }, completed]],
      ["reasoning", [added(0, "rs_fixture", "reasoning"), { type: "response.reasoning_summary_text.delta", output_index: 0, delta: "not repaired" }, completed]],
      ["tool arguments", [{ ...added(0, "fc_fixture", "function_call"), item: { id: "fc_fixture", type: "function_call", call_id: "call_fixture", name: "read", arguments: "" } },
        { type: "response.function_call_arguments.delta", output_index: 0, delta: "{}" }, completed]],
    ] as const) test(`missing ${name} IDs stay strict outside the repair whitelist`, async () => {
      expect(failure((await replay(wire([...events]))).outcome).message).toContain("missing item_id")
    })

    test("two interleaved tool calls preserve IDs, arguments, usage and next-request outputs", async () => {
      const first = { id: "fc_first", type: "function_call", call_id: "call_first", name: "first", arguments: "" }
      const second = { id: "fc_second", type: "function_call", call_id: "call_second", name: "second", arguments: "" }
      const events = [
        { type: "response.output_item.added", output_index: 1, item: first },
        { type: "response.output_item.added", output_index: 3, item: second },
        { type: "response.function_call_arguments.delta", output_index: 3, item_id: second.id, delta: '{"b":' },
        { type: "response.function_call_arguments.delta", output_index: 1, item_id: first.id, delta: '{"a":' },
        { type: "response.function_call_arguments.delta", output_index: 3, item_id: second.id, delta: "2}" },
        { type: "response.function_call_arguments.delta", output_index: 1, item_id: first.id, delta: "1}" },
        { type: "response.function_call_arguments.done", output_index: 3, item_id: second.id, arguments: '{"b":2}' },
        { type: "response.function_call_arguments.done", output_index: 1, item_id: first.id, arguments: '{"a":1}' },
        { type: "response.output_item.done", output_index: 1, item: { ...first, arguments: '{"a":1}' } },
        { type: "response.output_item.done", output_index: 3, item: { ...second, arguments: '{"b":2}' } },
        added(), delta(false), done, completed,
      ]
      const baselineEvents = events.map(event => event.type === "response.output_text.delta" ? delta() : event)
      const baseline = success(await parse(wire(baselineEvents)))
      const { outcome } = await replay(wire(events)), response = success(outcome)
      expect(response.events).toEqual(baseline.events)
      expect(response.usage).toEqual(baseline.usage)
      expect(response.toolCalls).toMatchObject([
        { id: "call_first", name: "first", input: { a: 1 }, providerMetadata: { openai: { itemId: "fc_first" } } },
        { id: "call_second", name: "second", input: { b: 2 }, providerMetadata: { openai: { itemId: "fc_second" } } },
      ])
      const nextBodies: any[] = []
      const nextRequest = LLM.request({ model, messages: [response.message,
        Message.tool({ id: "call_second", name: "second", result: { received: "second-result" } }),
        Message.tool({ id: "call_first", name: "first", result: { received: "first-result" } }),
      ] })
      const nextResponse = await Effect.runPromise(LLMClient.generate(nextRequest).pipe(Effect.provide(dynamicResponse((input: any) => Effect.sync(() => {
        nextBodies.push(JSON.parse(input.text))
        return input.respond(wire(textEvents()), { headers: sseHeaders })
      })))))
      expect(nextResponse.text).toBe(delta().delta)
      expect(nextBodies).toHaveLength(1)
      const replayedCalls = nextBodies[0].input.filter((item: any) => item.type === "function_call")
      expect(replayedCalls).toMatchObject([
        { id: "fc_first", call_id: "call_first", name: "first", arguments: '{"a":1}' },
        { id: "fc_second", call_id: "call_second", name: "second", arguments: '{"b":2}' },
      ])
      const outputs = nextBodies[0].input.filter((item: any) => item.type === "function_call_output")
      expect(outputs).toHaveLength(2)
      expect(Object.fromEntries(outputs.map((item: any) => [item.call_id, JSON.parse(item.output)]))).toEqual({
        call_first: { received: "first-result" }, call_second: { received: "second-result" },
      })
    })

    test("done-only tool calls and CR-delimited keepalives retain native behavior", async () => {
      const item = { id: "fc_done", type: "function_call", call_id: "call_done", name: "read", arguments: '{"path":"input.txt"}' }
      const text = "data: null\r\r" + wire([{ type: "response.output_item.done", output_index: 2, item }, ...textEvents()], "\r")
      const baseline = success(await parse(text)), repaired = success((await replay(text)).outcome)
      expect(repaired.events).toEqual(baseline.events)
      expect(repaired.toolCalls).toMatchObject([{ id: "call_done", name: "read", input: { path: "input.txt" } }])
    })

    for (const terminal of [
      { type: "response.failed", response: { id: "resp_failure", error: { message: "fixture provider failed", code: "server_error" } } },
      { type: "error", message: "fixture provider failed", code: "server_error" },
    ]) test(`${terminal.type} remains a native failure`, async () => {
      const text = wire([added(), delta(), terminal])
      expect(failure(await parse(text)).message).toContain("fixture provider failed")
      expect(failure((await replay(text)).outcome).message).toContain("fixture provider failed")
    })

    test("incomplete response preserves length finish and never finalizes partial tool input", async () => {
      const item = { id: "fc_partial", type: "function_call", call_id: "call_partial", name: "read", arguments: "" }
      const text = wire([
        { type: "response.output_item.added", output_index: 0, item },
        { type: "response.function_call_arguments.delta", output_index: 0, item_id: item.id, delta: '{"path":"unfinished' },
        { type: "response.incomplete", response: { id: "resp_incomplete", usage, incomplete_details: { reason: "max_output_tokens" } } },
      ])
      const baseline = success(await parse(text)), { outcome, records } = await replay(text), response = success(outcome)
      expect(response.events).toEqual(baseline.events)
      expect(response.finishReason.normalized).toBe("length")
      expect(response.finishReason.normalized).not.toBe("stop")
      expect(response.toolCalls).toEqual([])
      expect(response.text).toBe("")
      expect(records.find(record => record.kind === "end").terminal).toBe("response.incomplete")
    })

    test("EOF, partial frames and source errors cannot become a successful response", async () => {
      for (const [text, error] of [
        [wire([added(), delta()]), undefined],
        [wire([added()]) + 'data: {"type":', undefined],
        [wire([added(), delta()]), new Error("fixture connection reset")],
      ] as const) {
        failure(await parse(chunks(text, 7, error)))
        const { outcome, records } = await replay(text, "repair", error)
        failure(outcome)
        const end = records.find(record => record.kind === "end")
        expect(end.complete).toBe(false)
        expect(end.terminal).toBe(null)
      }
    })

    test("a completed response with truncated arguments never emits an executable tool call", async () => {
      const item = { id: "fc_bad", type: "function_call", call_id: "call_bad", name: "read", arguments: '{"path":"unfinished' }
      const text = wire([{ type: "response.output_item.done", output_index: 0, item }, completed])
      const baseline = success(await parse(text)), response = success((await replay(text)).outcome)
      expect(response.events).toEqual(baseline.events)
      expect(response.toolCalls).toEqual([])
      expect(response.events.find((event: any) => event.type === "tool-input-error")).toMatchObject({ id: "call_bad", raw: item.arguments })
      expect(response.finishReason.normalized).not.toBe("stop")
    })
  })
}
