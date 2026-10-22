/** Synchronous, cross-process bounded JSONL capture in a task-owned directory. */
import fs from "node:fs"
import path from "node:path"
import {redact} from "./responses_transport.mjs"

const sleepCell = new Int32Array(new SharedArrayBuffer(4))
function open(file, flags) {
  const fd=fs.openSync(file,flags|fs.constants.O_NOFOLLOW,0o600)
  const info=fs.fstatSync(fd)
  if (!info.isFile() || info.nlink!==1) {fs.closeSync(fd);throw new Error("Responses capture requires an owned regular file")}
  return fd
}
function readStatus(file) {
  let fd
  try {fd=open(file,fs.constants.O_RDONLY)} catch(e) {if(e.code==="ENOENT")return null;throw e}
  try {
    if(fs.fstatSync(fd).size>65536)throw new Error("Responses capture status exceeds limit")
    return JSON.parse(fs.readFileSync(fd,"utf8"))
  } finally {fs.closeSync(fd)}
}
export function createRecorder(directory,{identity,secrets=[],maxRequestBytes=4*1024*1024,maxRunBytes=16*1024*1024}={}) {
  const root=path.resolve(directory), lock=path.join(root,".lock"), statusFile=path.join(root,"status.json")
  if(fs.realpathSync(root)!==root || !fs.lstatSync(root).isDirectory())throw new Error("Responses capture directory must be owned")
  const reserve=Math.min(65536,Math.floor(maxRunBytes/2)), logLimit=maxRunBytes-reserve
  return function record(value) {
    let lockFd
    for(let n=0;n<500;n++) {
      try {lockFd=fs.openSync(lock,"wx",0o600);break}
      catch(e){if(e.code!=="EEXIST")throw e;Atomics.wait(sleepCell,0,0,2)}
    }
    if(lockFd===undefined)throw new Error("Responses capture lock unavailable")
    let logFd
    try {
      const status=readStatus(statusFile)||{schema:"responses.capture.v1",bytes:0,truncated:false,requests:{}}
      logFd=open(path.join(root,"http.jsonl"),fs.constants.O_WRONLY|fs.constants.O_APPEND|fs.constants.O_CREAT)
      if(fs.fstatSync(logFd).size!==status.bytes)throw new Error("Responses capture size disagrees with quota")
      const id=value.request_id || `process-${process.pid}`
      const request=status.requests[id] || {bytes:0,truncated:false}
      const line=JSON.stringify(redact({pid:process.pid,overlay_sha256:identity,...value},secrets))+"\n"
      const bytes=Buffer.byteLength(line)
      if((!status.requests[id] && Object.keys(status.requests).length>=128)
        || request.bytes+bytes>maxRequestBytes || status.bytes+bytes>logLimit) {
        status.truncated=true;request.truncated=true
      } else {
        fs.writeSync(logFd,line);status.bytes+=bytes;request.bytes+=bytes
      }
      if(Object.keys(status.requests).length<128 || status.requests[id]) status.requests[id]=request
      const encoded=JSON.stringify(status)
      if(Buffer.byteLength(encoded)>reserve)throw new Error("Responses capture status capacity exceeded")
      const fd=open(statusFile,fs.constants.O_WRONLY|fs.constants.O_CREAT)
      try {fs.ftruncateSync(fd,0);fs.writeSync(fd,encoded)} finally {fs.closeSync(fd)}
    } finally {
      if(logFd!==undefined)fs.closeSync(logFd)
      fs.closeSync(lockFd);fs.unlinkSync(lock)
    }
  }
}
