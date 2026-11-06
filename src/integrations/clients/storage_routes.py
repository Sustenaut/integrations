"""Generated from openapi/storage.json (Sustenaut/storage's openapi.json at e7f3d90f9c75bc935fb2a6e495507f4805be3193)
by clients/js/generate.mjs: do not edit. Each operation's route, for storage.py."""

# fmt: off
SPEC = {"repository":"Sustenaut/storage","commit":"e7f3d90f9c75bc935fb2a6e495507f4805be3193","sha256":"05bb79f3bdbf6e380cb678b41d4c8a01d9005c7c9bd1c77927645f209f392ee7"}
ROUTES = {
  "getPhoto": {"method":"GET","path":"/{tenant}/p/{id}/{size}","slashes":[],"query":[],"headers":[],"body":"","answer":"bytes"},
  "headPhoto": {"method":"HEAD","path":"/{tenant}/p/{id}/{size}","slashes":[],"query":[],"headers":[],"body":"","answer":"none"},
  "uploadPhoto": {"method":"POST","path":"/{tenant}/upload","slashes":[],"query":[],"headers":[],"body":"multipart/form-data","answer":"json"},
  "getFile": {"method":"GET","path":"/{tenant}/f/{id}/{name}","slashes":[],"query":[],"headers":[],"body":"","answer":"bytes"},
  "headFile": {"method":"HEAD","path":"/{tenant}/f/{id}/{name}","slashes":[],"query":[],"headers":[],"body":"","answer":"none"},
  "uploadFile": {"method":"POST","path":"/{tenant}/files","slashes":[],"query":[],"headers":[],"body":"multipart/form-data","answer":"json"},
  "getHead": {"method":"GET","path":"/{tenant}/head/{name}","slashes":[],"query":[],"headers":[],"body":"","answer":"bytes"},
  "headHead": {"method":"HEAD","path":"/{tenant}/head/{name}","slashes":[],"query":[],"headers":[],"body":"","answer":"none"},
  "moveHead": {"method":"PUT","path":"/{tenant}/head/{name}","slashes":[],"query":[],"headers":["If-Match","If-None-Match"],"body":"application/json","answer":"json"},
  "getHeadLog": {"method":"GET","path":"/{tenant}/head/{name}/log","slashes":[],"query":[],"headers":[],"body":"","answer":"json"},
  "getInbox": {"method":"GET","path":"/{tenant}/inbox/{path}","slashes":["path"],"query":[],"headers":[],"body":"","answer":"bytes"},
  "headInbox": {"method":"HEAD","path":"/{tenant}/inbox/{path}","slashes":["path"],"query":[],"headers":[],"body":"","answer":"none"},
  "putInbox": {"method":"PUT","path":"/{tenant}/inbox/{path}","slashes":["path"],"query":[],"headers":[],"body":"application/avro","answer":"json"},
  "deleteInbox": {"method":"DELETE","path":"/{tenant}/inbox/{path}","slashes":["path"],"query":[],"headers":[],"body":"","answer":"none"},
  "getRejected": {"method":"GET","path":"/{tenant}/rejected/{path}","slashes":["path"],"query":[],"headers":[],"body":"","answer":"bytes"},
  "headRejected": {"method":"HEAD","path":"/{tenant}/rejected/{path}","slashes":["path"],"query":[],"headers":[],"body":"","answer":"none"},
  "putRejected": {"method":"PUT","path":"/{tenant}/rejected/{path}","slashes":["path"],"query":[],"headers":[],"body":"application/avro","answer":"json"},
  "deleteRejected": {"method":"DELETE","path":"/{tenant}/rejected/{path}","slashes":["path"],"query":[],"headers":[],"body":"","answer":"none"},
  "getBatches": {"method":"GET","path":"/{tenant}/batches/{name}","slashes":[],"query":[],"headers":[],"body":"","answer":"json"},
  "headBatches": {"method":"HEAD","path":"/{tenant}/batches/{name}","slashes":[],"query":[],"headers":[],"body":"","answer":"none"},
  "putBatches": {"method":"PUT","path":"/{tenant}/batches/{name}","slashes":[],"query":[],"headers":[],"body":"application/json","answer":"json"},
  "deleteBatches": {"method":"DELETE","path":"/{tenant}/batches/{name}","slashes":[],"query":[],"headers":[],"body":"","answer":"none"},
  "list": {"method":"GET","path":"/{tenant}/list","slashes":[],"query":["prefix","limit","cursor"],"headers":[],"body":"","answer":"json"},
  "clean": {"method":"POST","path":"/_clean","slashes":[],"query":[],"headers":[],"body":"","answer":"json"},
}
