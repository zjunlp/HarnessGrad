/** Trusted preload; executed by a separately verified launcher, never copied into Bun. */
import fs from "node:fs"
import {fileURLToPath} from "node:url"
import childProcess from "node:child_process"
import {syncBuiltinESMExports} from "node:module"
import {observeFetch} from "./responses_transport.mjs"
import {createRecorder} from "./responses_capture.mjs"

const manifest=JSON.parse(fs.readFileSync(new URL("./manifest.json",import.meta.url),"utf8"))
const spec=manifest.identity
const directory=process.env.HG_RESPONSES_ARTIFACT_DIR
if(!directory)throw new Error("Responses overlay requires a task capture directory")
const secrets=Object.entries(process.env).filter(([k])=>/API_KEY|SECRET|PASSWORD|ACCESS_TOKEN/.test(k)).map(([,v])=>v).filter(Boolean)
const record=createRecorder(directory,{identity:spec.sha256,secrets,...spec.limits})
record({kind:"preload",role:process.argv.includes("serve")?"serve":"cli",mode:spec.mode})
const preload=fileURLToPath(import.meta.url)
const nativeSpawn=childProcess.spawn
childProcess.spawn=function(command,args,options) {
  // Pinned standalone recreates [process.execPath, entry, serve, ...], omitting
  // execArgv. Only propagate to this declared source entry, never arbitrary tools.
  if(command===process.execPath && Array.isArray(args) && args[0]===process.env.HG_HARNESS_ENTRYPOINT
    && args[1]==="serve") {
    return Reflect.apply(nativeSpawn,this,[command,["--preload",preload,...args],options])
  }
  return Reflect.apply(nativeSpawn,this,arguments)
}
syncBuiltinESMExports()
globalThis.fetch=observeFetch(globalThis.fetch.bind(globalThis),{
  route:spec.route,mode:spec.mode,record,secrets,maxEventBytes:spec.limits.maxEventBytes,maxEvents:spec.limits.maxEvents,
})
