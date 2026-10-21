/** Route-scoped Responses observation and narrowly evidenced wire repair.
 * No model client, retries, package installation or harness/scorer dependencies.
 */
import { randomUUID } from "node:crypto"

export function createState() {
  return {items: new Map(), ids: new Set(), created: false, responseId: null, terminal: null, currentMessage: null}
}

const fail = (message) => { throw new Error(`Responses compatibility: ${message}`) }
const terminalTypes = new Set(["response.completed", "response.failed", "response.incomplete", "error"])
export function normalizeEvent(event, state, {repairMissingItemId = false} = {}) {
  if (!repairMissingItemId) return event
  if (!event || typeof event !== "object" || typeof event.type !== "string") return event
  if (state.terminal) fail("event after response terminal")
  if (event.type === "response.created") {
    if (state.created || state.items.size) fail("response state reused")
    state.created = true
    if (typeof event.response?.id === "string" && event.response.id.trim()) state.responseId = event.response.id
  }
  if (state.responseId !== null && (
    (Object.hasOwn(event,"response_id") && event.response_id !== state.responseId) ||
    (event.response && Object.hasOwn(event.response,"id") && event.response.id !== state.responseId)
  )) fail("conflicting response identity")
  if (terminalTypes.has(event.type)) state.terminal = event.type
  if (event.type === "response.output_item.added") {
    const {output_index: index, item} = event
    if (!Number.isSafeInteger(index) || index < 0 || !item || typeof item.id !== "string" || !item.id.trim()) fail("invalid output item mapping")
    if (state.items.has(index) || state.ids.has(item.id)) fail("duplicate output item mapping")
    if (item.type === "message") {
      if (state.currentMessage !== null) state.items.get(state.currentMessage).active = false
      state.currentMessage = index
    }
    state.items.set(index, {id: item.id, type: item.type, active: true})
    state.ids.add(item.id)
  }
  const item = state.items.get(event.output_index)
  // Native normalization can overwrite a supplied conflicting ID. Refuse it here.
  if (Object.hasOwn(event, "item_id") && item && event.item_id !== item.id) fail("conflicting supplied item_id")
  if (event.type === "response.output_item.done") {
    if (item && (event.item?.id !== item.id || event.item?.type !== item.type)) fail("conflicting completed output item")
    if (item) item.active = false
  }
  if (event.type !== "response.output_text.delta") return event
  if (!item || !item.active || item.type !== "message" || state.currentMessage !== event.output_index) fail("no active message mapping")
  if (Object.hasOwn(event, "item_id")) return event
  return {...event, item_id: item.id}
}

export function redact(value, secrets = []) {
  if (typeof value === "string") {
    for (const secret of secrets) if (secret) value = value.split(secret).join("<redacted>")
    return value
  }
  if (Array.isArray(value)) return value.map(v => redact(v, secrets))
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k,v]) =>
    [k, /authorization|api[-_]?key|secret|password|access[-_]?token|cookie/i.test(k) ? "<redacted>" : redact(v,secrets)]))
  return value
}

/** Wrap a fetch implementation once. The original request is sent exactly once.
 * The consumer drives the same response reader; no tee, clone drain, or retry.
 */
export function observeFetch(fetchImpl, options = {}) {
  const {route, mode = "observe", record = () => {}, secrets = [], maxEventBytes = 1024 * 1024,
    maxEvents = 20000, maxBodyBytes = 4 * 1024 * 1024} = options
  if (!route || !["observe","repair"].includes(mode)) throw new Error("Invalid Responses transport options")
  return async function observedFetch(input, init) {
    const url = typeof input === "string" || input instanceof URL ? String(input) : input.url
    if (url !== route) return fetchImpl(input, init)
    const requestId = randomUUID()
    let captureError
    const emit = value => {
      if (captureError) return
      try { record(redact({request_id:requestId, ...value}, secrets)) }
      catch (error) { captureError = error }
    }
    let body = init?.body, requestComplete = true
    // Effect's native HTTP client sends static Uint8Array JSON. Inspect those
    // bytes without consuming a Request/stream or changing the outgoing body.
    if (body instanceof ArrayBuffer || ArrayBuffer.isView(body)) {
      const bytes = body instanceof ArrayBuffer ? new Uint8Array(body) : new Uint8Array(body.buffer,body.byteOffset,body.byteLength)
      if (bytes.byteLength > maxBodyBytes) { body = {omitted:"request exceeds capture limit"}; requestComplete = false }
      else {
        try { body = new TextDecoder("utf-8",{fatal:true}).decode(bytes) }
        catch { body = {omitted:"request is not valid UTF-8"}; requestComplete = false }
      }
    }
    if (typeof body === "string") {
      if (Buffer.byteLength(body) > maxBodyBytes) { body = {omitted:"request exceeds capture limit"}; requestComplete = false }
      else { try { body = JSON.parse(body) } catch { /* retain bounded text */ } }
    } else if (requestComplete && (body != null || input instanceof Request)) { body = {omitted:"stream or Request body not consumed by observer"}; requestComplete = false }
    emit({kind:"request",route,method:init?.method || input?.method || "GET",body})
    let original
    try { original = await fetchImpl(input, init) }
    catch (e) { emit({kind:"fetch_error",message:String(e)}); throw e }
    emit({kind:"response",status:original.status,server_request_id:original.headers.get("x-request-id"),
      content_type:original.headers.get("content-type"),retry_policy:original.headers.get("x-should-retry")})
    if (original.url && original.url !== route) {
      emit({kind:"end",reason:"redirect_outside_route",complete:false,terminal:null})
      try { await original.body?.cancel() } catch { /* route rejection wins */ }
      fail("redirect outside the configured route")
    }
    if (!original.body) { emit({kind:"end",reason:"no_body",complete:false,terminal:null}); return original }
    const sse = original.headers.get("content-type")?.split(";")[0].trim() === "text/event-stream"
    const repair = mode === "repair" && sse && original.ok
    const state = createState(), reader = original.body.getReader(), decoder = new TextDecoder("utf-8",{fatal:true})
    let pending = "", nonSse = "", count = 0, terminal = null, ended = false, parseFailed = false, bodyBytes = 0
    const end = (reason, error) => {
      if (ended) return
      ended = true
      if (!sse && nonSse) {
        let value = nonSse
        try { value = JSON.parse(nonSse) } catch { /* bounded diagnostic */ }
        emit({kind:"body",body:value,complete:!parseFailed && reason === "eof" && bodyBytes <= maxBodyBytes})
      }
      emit({kind:"end",reason,terminal,complete:requestComplete && !parseFailed && (sse ? Boolean(terminal) : reason === "eof" && bodyBytes <= maxBodyBytes),
        events:count,...(error ? {error:String(error)} : {})})
    }
    function consumeFrame(raw) {
      const lines = raw.split(/\r\n|\r|\n/), data = lines.filter(l=>l.startsWith("data:")).map(l=>l.slice(5).replace(/^ /, "")).join("\n")
      if (!data) return raw
      if (data === "[DONE]") {
        if (repair && !terminal) fail("DONE without terminal event")
        return raw
      }
      if (++count > maxEvents) fail("event count limit")
      const event = JSON.parse(data)
      let normalized
      try { normalized = normalizeEvent(event,state,{repairMissingItemId:repair}) }
      catch (error) { emit({kind:"event",ordinal:count,original:event,normalization_error:String(error)}); throw error }
      if (terminalTypes.has(event?.type)) terminal = event.type
      const changed = normalized !== event
      emit({kind:"event",ordinal:count,original:event,normalized,...(changed ? {repaired:true,mapping_source:"response.output_item.added"} : {})})
      if (!changed) return raw
      // Only the data lines change; comments, event names and line endings survive.
      let written = false
      const newline = /\r\n|\r|\n/.exec(raw)?.[0] || "\n"
      return lines.filter(l=>!l.startsWith("data:") || (!written && (written=true)))
        .map(l=>l.startsWith("data:") ? `data: ${JSON.stringify(normalized)}` : l).join(newline)
    }
    function boundary(text, final) {
      let start=0
      for(let n=0;n<text.length;n++) {
        if(text[n]!=="\r" && text[n]!=="\n")continue
        if(text[n]==="\r" && n+1===text.length && !final)return null
        const width=text[n]==="\r" && text[n+1]==="\n"?2:1
        if(n===start)return {end:n,size:n+width}
        start=n+width;n+=width-1
      }
      return null
    }
    function processChunk(chunk, final=false) {
      if (parseFailed) return chunk
      const text = decoder.decode(chunk,{stream:!final})
      if (!sse) {
        bodyBytes += chunk.byteLength
        if (bodyBytes <= maxBodyBytes) nonSse += text
        return chunk
      }
      pending += text
      let output = "", match
      while ((match = boundary(pending,final))) {
        const raw = pending.slice(0,match.end), separator = pending.slice(match.end,match.size)
        pending = pending.slice(match.size)
        if (Buffer.byteLength(raw) > maxEventBytes) fail("event byte limit")
        output += consumeFrame(raw) + separator
      }
      if (Buffer.byteLength(pending) > maxEventBytes) fail("event byte limit")
      return repair ? new TextEncoder().encode(output) : chunk
    }
    const stream = new ReadableStream({
      async pull(controller) {
        try {
          // A partial frame yields no output. Keep reading within this pull until
          // one frame is available; do not rely on another consumer pull.
          while (true) {
          if(captureError)throw new Error("Responses capture failed",{cause:captureError})
          const {done,value} = await reader.read()
          if (done) {
            let tail=new Uint8Array()
            try { tail=processChunk(tail,true) }
            catch(e) {
              parseFailed=true;pending="";emit({kind:"protocol_error",message:String(e)})
              if(repair)throw e
            }
            if(tail.byteLength)controller.enqueue(tail)
            if (sse && (pending.trim() || !terminal)) {
              parseFailed = true
              if (repair) fail("truncated response stream")
            }
            end("eof")
            if(captureError)throw new Error("Responses capture failed",{cause:captureError})
            controller.close(); return
          }
          let out
          try { out=processChunk(value) }
          catch (e) {
            parseFailed=true; pending=""; emit({kind:"protocol_error",message:String(e)})
            if (repair) throw e
            out=value
          }
          if (out.byteLength) { controller.enqueue(out); return }
          }
        } catch (e) {
          parseFailed=true; end("error",e); controller.error(e)
          try { await reader.cancel(e) } catch { /* original read error wins */ }
        }
      },
      async cancel(reason) { try { end("cancel") } finally { await reader.cancel(reason) } },
    })
    const headers=new Headers(original.headers)
    if(repair)headers.delete("content-length")
    return new Response(stream,{status:original.status,statusText:original.statusText,headers})
  }
}
