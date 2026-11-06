# Shared test vectors

Each file is one call, run by both callers: `tests/steps/test_vectors.py` for
`integrations.calls` and `clients/js/test/vectors.test.mjs` for `clients/js/caller.js`.

- `responses` is in `Replay`'s format: answers by `"METHOD url"`, a list answering in
  turn (its last for good), `{"error": ...}` for no answer, `"json"` or `"text"` for the body, and
  `"headers"` for its ETag or Retry-After.
- `request` is what `call` is given (`body` as text), with `request_id`, `modes` and
  `refused` (`{"key", "equals"}`: refused when the body's `key` equals that) when the vector needs them.
- The caller is `repo` `a-repo`, `app` `web`, with ids `id-1`, and its sleeps are kept, in seconds.
  Its random source gives `random` (default 1.0, the top of the jitter's range, so a
  backoff's waits are exactly 2**n).
- `expect` is the answer's `status`, the error raised (`Unavailable` or `null`), how many
  requests were `sent`, the `sleeps`, and the record's fields that must match.

`sink.avro` is a file `clients/js/caller.js`'s `StorageSink` writes, and `sink.json`
what is in it; `test_vectors.py` reads it with `integrations.avrofile`. Rewrite both with
`WRITE_VECTORS=1 npm test` in `clients/js` when the sink's output changes on purpose.
