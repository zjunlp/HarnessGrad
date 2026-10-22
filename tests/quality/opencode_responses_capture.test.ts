import {expect,test} from "bun:test"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import {createRecorder} from "../../eval/service/responses_capture.mjs"

test("shared recorder caps requests/run including two independent writers and redacts",()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"hg-capture-test-"))
  try {
    const a=createRecorder(dir,{identity:"a",secrets:["private-value"],maxRequestBytes:600,maxRunBytes:2048})
    const b=createRecorder(dir,{identity:"a",maxRequestBytes:600,maxRunBytes:2048})
    a({kind:"request",request_id:"first",body:{apiKey:"private-value",text:"private-value"}})
    for(let n=0;n<30;n++) (n%2?a:b)({kind:"event",request_id:"r"+Math.floor(n/6),text:"x".repeat(200)})
    const text=fs.readFileSync(path.join(dir,"http.jsonl"),"utf8")
    expect(text).not.toContain("private-value")
    expect(Buffer.byteLength(text)+fs.statSync(path.join(dir,"status.json")).size).toBeLessThanOrEqual(2048)
    const status=JSON.parse(fs.readFileSync(path.join(dir,"status.json"),"utf8"))
    expect(status.truncated).toBe(true)
    expect(Object.values(status.requests).every((r:any)=>r.bytes<=600)).toBe(true)
  } finally {fs.rmSync(dir,{recursive:true,force:true})}
})
test("recorder refuses symlink outputs instead of overwriting another file",()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"hg-capture-test-"))
  try {
    fs.writeFileSync(path.join(dir,"victim"),"untouched")
    fs.symlinkSync(path.join(dir,"victim"),path.join(dir,"http.jsonl"))
    const record=createRecorder(dir,{identity:"a"})
    expect(()=>record({kind:"request",request_id:"r"})).toThrow()
    expect(fs.readFileSync(path.join(dir,"victim"),"utf8")).toBe("untouched")
  } finally {fs.rmSync(dir,{recursive:true,force:true})}
})
