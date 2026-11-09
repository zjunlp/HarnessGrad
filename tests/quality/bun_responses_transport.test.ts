import { describe, expect, test } from "bun:test"
import { createState, normalizeEvent, observeFetch } from "../../eval/service/responses_transport.mjs"

const route = "https://coding.tu-zi.com/v1/responses"
const added = (index = 0, id = "msg_1", type = "message") => ({type:"response.output_item.added", output_index:index, item:{id,type}})
const delta = (extra = {}) => ({type:"response.output_text.delta", output_index:0, content_index:0, delta:"\u4f60\u597d\u{1f642}", ...extra})
const completed = {type:"response.completed",response:{id:"resp_1",status:"completed",usage:{input_tokens:2,output_tokens:3,total_tokens:5}}}
const frame = (e: any) => `event: ${e.type}\r\ndata: ${JSON.stringify(e)}\r\n\r\n`
const options = {repairMissingItemId:true}

test("only an established current message restores a missing id without mutating input", () => {
  const s=createState(); normalizeEvent(added(),s,options)
  const input=delta()
  expect(normalizeEvent(input,s,options)).toEqual({...input,item_id:"msg_1"})
  expect(input).not.toHaveProperty("item_id")
})
test("strict default preserves even missing fields and does not repair", () => {
  const s=createState(); normalizeEvent(added(),s,{})
  const input=delta(); expect(normalizeEvent(input,s,{})).toBe(input)
})
describe("unprovable or inconsistent association is refused", () => {
  for (const [name,events] of [
    ["orphan", [delta()]],
    ["wrong type", [added(0,"fc_1","function_call"),delta()]],
    ["empty id", [added(0,""),delta()]],
    ["negative index", [added(-1),delta({output_index:-1})]],
    ["duplicate index", [added(),added(0,"other"),delta()]],
    ["duplicate id", [added(),added(1,"msg_1")]],
    ["present conflicting id", [added(),delta({item_id:"other"})]],
    ["empty present id", [added(),delta({item_id:""})]],
    ["late delta", [added(),{...added(),type:"response.output_item.done"},delta()]],
    ["superseded message", [added(),added(1,"msg_2"),delta()]],
    ["after terminal", [added(),completed,delta()]],
    ["response reused", [{type:"response.created",response:{id:"a"}},{type:"response.created",response:{id:"b"}}]],
  ] as const) test(name, () => {
    const s=createState(); expect(()=>events.forEach(e=>normalizeEvent(e,s,options))).toThrow()
  })
})
test("separate responses cannot share mapping", () => {
  normalizeEvent(added(),createState(),options)
  expect(()=>normalizeEvent(delta(),createState(),options)).toThrow()
})
test("a known response identity rejects foreign deltas and terminal events", () => {
  for(const foreign of [delta({response_id:"resp_other"}),{...completed,response:{...completed.response,id:"resp_other"}}]) {
    const s=createState()
    normalizeEvent({type:"response.created",response:{id:"resp_1"}},s,options)
    normalizeEvent(added(),s,options)
    expect(()=>normalizeEvent(foreign,s,options)).toThrow("response identity")
  }
  const s=createState()
  normalizeEvent({type:"response.created",response:{id:"resp_1"}},s,options)
  normalizeEvent(added(),s,options)
  expect(normalizeEvent(delta({response_id:"resp_1"}),s,options).item_id).toBe("msg_1")
  expect(normalizeEvent(delta(),s,options).item_id).toBe("msg_1")
})
test("omitted request bodies cannot produce a complete capture",async()=>{
  for(const [input,init] of [
    [route,{method:"POST",body:"x".repeat(9)}],
    [new Request(route,{method:"POST",body:"request"}),undefined],
    [route,{method:"POST",body:new ReadableStream()}],
  ] as const) {
    const records:any[]=[];let calls=0
    const fetch=observeFetch(async(actualInput,actualInit)=>{
      calls++;expect(actualInput).toBe(input);expect(actualInit).toBe(init)
      return response(frame(completed))
    },{route,mode:"repair",maxBodyBytes:8,record:(r:any)=>records.push(r)})
    expect(await(await fetch(input,init)).text()).toBe(frame(completed))
    expect(calls).toBe(1)
    expect(records.find(r=>r.kind==="request").body.omitted).toBeTruthy()
    expect(records.find(r=>r.kind==="end").complete).toBe(false)
  }
})
test("static UTF-8 byte bodies are captured without changing the sent request",async()=>{
  const original={model:"offline",input:"\u4f60\u597d\u{1f642}"}
  const bytes=new TextEncoder().encode(JSON.stringify(original))
  const padded=new Uint8Array(bytes.length+4);padded.set(bytes,2)
  for(const body of [bytes,bytes.buffer,padded.subarray(2,-2)]) {
    const records:any[]=[]
    const fetch=observeFetch(async(_,init)=>{
      expect(init.body).toBe(body);return response(frame(completed))
    },{route,mode:"repair",record:(r:any)=>records.push(r)})
    await(await fetch(route,{method:"POST",body})).text()
    expect(records.find(r=>r.kind==="request").body).toEqual(original)
    expect(records.find(r=>r.kind==="end").complete).toBe(true)
  }
})
test("interleaved function arguments and call ids are unchanged", () => {
  const s=createState()
  for (const e of [added(0,"fc_a","function_call"),added(1,"fc_b","function_call"),
    {type:"response.function_call_arguments.delta",output_index:1,item_id:"fc_b",delta:'{"b":'},
    {type:"response.function_call_arguments.delta",output_index:0,item_id:"fc_a",delta:'{"a":1}'},
    {type:"response.function_call_arguments.delta",output_index:1,item_id:"fc_b",delta:'2}'},
    {type:"response.output_item.done",output_index:1,item:{id:"fc_b",type:"function_call",call_id:"call_b",arguments:'{"b":2}'}},
  ]) expect(normalizeEvent(e,s,options)).toBe(e)
})

function response(text: string, step=1) {
  const bytes=new TextEncoder().encode(text); let n=0
  return new Response(new ReadableStream({pull(c){if(n===bytes.length)c.close();else{c.enqueue(bytes.slice(n,n+step));n+=Math.min(step,bytes.length-n)}}}),
    {headers:{"content-type":"text/event-stream","x-request-id":"server-1"}})
}
test("repair survives UTF-8 byte boundaries, records original and normalized, sends once", async () => {
  let count=0; const records:any[]=[]
  const fetch=observeFetch(async()=>{count++;return response([added(),delta(),completed].map(frame).join(""))},
    {route,mode:"repair",record:(v:any)=>records.push(v)})
  const result=await fetch(route,{method:"POST",headers:{Authorization:"Bearer private-value"},body:JSON.stringify({model:"fixture",stream:true})})
  const body=await result.text()
  expect(count).toBe(1); expect(body).toContain('"item_id":"msg_1"'); expect(body).toContain("\u4f60\u597d\u{1f642}")
  const changed=records.find(r=>r.kind==="event" && r.repaired)
  expect(changed.original).not.toHaveProperty("item_id")
  expect(changed.normalized.item_id).toBe("msg_1")
  expect(records.find(r=>r.kind==="end").terminal).toBe("response.completed")
  expect(JSON.stringify(records)).not.toContain("private-value")
})
test("compliant events and non-target routes are byte-for-byte passthrough",async()=>{
  const input=[added(),delta({item_id:"msg_1"}),completed].map(frame).join("")
  for(const mode of ["observe","repair"]) {
    const fetch=observeFetch(async()=>response(input,3),{route,mode})
    expect(await(await fetch(route)).text()).toBe(input)
    const other=observeFetch(async()=>response(frame(delta())),{route,mode})
    expect(await(await other("https://other.example/v1/responses")).text()).toBe(frame(delta()))
  }
})
test("observe does not reject an orphan or change bytes",async()=>{
  const text=frame(delta()); const records:any[]=[]
  const fetch=observeFetch(async()=>response(text),{route,mode:"observe",record:(r:any)=>records.push(r)})
  expect(await(await fetch(route)).text()).toBe(text)
  expect(records.find(r=>r.kind==="end").complete).toBe(false)
})
test("repair refuses incomplete SSE and premature EOF",async()=>{
  for(const text of [frame(added())+frame(delta()), frame(added())+'data: {"type":']) {
    const fetch=observeFetch(async()=>response(text),{route,mode:"repair"})
    await expect((await fetch(route)).text()).rejects.toThrow()
  }
})
test("normal consumer cancellation after terminal is distinct from truncation",async()=>{
  const records:any[]=[]
  const fetch=observeFetch(async()=>new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode(frame(completed)))}}),{headers:{"content-type":"text/event-stream"}}),
    {route,mode:"repair",record:(r:any)=>records.push(r)})
  const reader=(await fetch(route)).body!.getReader(); await reader.read(); await reader.cancel()
  const end=records.find(r=>r.kind==="end"); expect(end.complete).toBe(true); expect(end.reason).toBe("cancel")
})
test("error HTTP bodies and fetch errors are redacted without being swallowed",async()=>{
  const records:any[]=[]
  const fetch=observeFetch(async()=>new Response(JSON.stringify({error:{message:"private-value forbidden"}}),{status:403}),
    {route,mode:"repair",secrets:["private-value"],record:(r:any)=>records.push(r)})
  const r=await fetch(route); expect(r.status).toBe(403); expect(await r.text()).toContain("private-value")
  expect(JSON.stringify(records)).not.toContain("private-value"); expect(JSON.stringify(records)).toContain("forbidden")
  const fail=observeFetch(async()=>{throw new Error("private-value failed")},{route,record:(r:any)=>records.push(r),secrets:["private-value"]})
  await expect(fail(route)).rejects.toThrow("private-value")
  expect(JSON.stringify(records)).not.toContain("private-value")
})
test("oversized incomplete event is bounded and fails repair",async()=>{
  const fetch=observeFetch(async()=>response('data: '+"x".repeat(300),50),{route,mode:"repair",maxEventBytes:128})
  await expect((await fetch(route)).text()).rejects.toThrow()
})
test("legitimate done-only tools and retired-message completion are passthrough",()=>{
  const s=createState()
  const tool={type:"response.output_item.done",output_index:2,item:{id:"fc_only",type:"function_call",call_id:"call_only",name:"read",arguments:'{"path":"a"}'}}
  expect(normalizeEvent(tool,s,options)).toBe(tool)
  normalizeEvent(added(),s,options); normalizeEvent(added(1,"msg_2"),s,options)
  const done={type:"response.output_item.done",output_index:0,item:{id:"msg_1",type:"message"}}
  expect(normalizeEvent(done,s,options)).toBe(done)
})
test("keepalive JSON and bare-CR SSE accepted by native parser remain usable",async()=>{
  const input='data: null\r\r'+[added(),delta(),completed].map(e=>frame(e).replaceAll("\r\n","\r")).join("")
  const fetch=observeFetch(async()=>response(input),{route,mode:"repair"})
  const output=await(await fetch(route)).text()
  expect(output).toContain('"item_id":"msg_1"'); expect(output).toContain('data: null\r\r')
})
test("a rejected event is retained and capture failure cannot skip upstream cancellation",async()=>{
  const records:any[]=[]
  const fetch=observeFetch(async()=>response(frame(delta({delta:"diagnostic text"}))),{route,mode:"repair",record:(r:any)=>records.push(r)})
  await expect((await fetch(route)).text()).rejects.toThrow()
  expect(records.find(r=>r.kind==="event").original.delta).toBe("diagnostic text")
  let cancelled=false
  const faulty=observeFetch(async()=>new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode(frame(completed)))},cancel(){cancelled=true}}),{headers:{"content-type":"text/event-stream"}}),
    {route,mode:"repair",record:(r:any)=>{if(r.kind==="end")throw new Error("disk full")}})
  const reader=(await faulty(route)).body!.getReader();await reader.read()
  try {await reader.cancel()} catch {}
  expect(cancelled).toBe(true)
})
test("observe preserves invalid UTF-8 bytes even when decoding fails at EOF",async()=>{
  for(const contentType of ["text/event-stream","application/json"]) {
    const bytes=new Uint8Array([97,0xe4,0xbd]);const records:any[]=[]
    const fetch=observeFetch(async()=>new Response(bytes,{headers:{"content-type":contentType}}),
      {route,mode:"observe",record:(r:any)=>records.push(r)})
    expect(new Uint8Array(await(await fetch(route)).arrayBuffer())).toEqual(bytes)
    expect(records.find(r=>r.kind==="end").complete).toBe(false)
  }
})
test("cancelling an HTTP error body before EOF is incomplete",async()=>{
  const records:any[]=[]
  const fetch=observeFetch(async()=>new Response("forbidden",{status:403}),
    {route,mode:"repair",record:(r:any)=>records.push(r)})
  await(await fetch(route)).body!.cancel()
  expect(records.find(r=>r.kind==="end").complete).toBe(false)
})
test("a redirected response cannot be repaired under the original route identity",async()=>{
  const records:any[]=[]
  const upstream=response([added(),delta(),completed].map(frame).join(""))
  Object.defineProperty(upstream,"url",{value:"https://other.example/v1/responses"})
  const fetch=observeFetch(async()=>upstream,{route,mode:"repair",record:(r:any)=>records.push(r)})
  await expect(fetch(route)).rejects.toThrow("redirect")
  expect(records.some(r=>r.repaired)).toBe(false)
  expect(records.find(r=>r.kind==="end").complete).toBe(false)
})
