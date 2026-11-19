# Shared test vectors

Each file is one call, run by both callers: `tests/steps/test_vectors.py` for
`integrations.calls` and `clients/js/test/vectors.steps.mjs` for `clients/js/caller.js`.

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

## Cassettes

`cassettes/*.json` run on both transports: `tests/steps/test_vectors.py` for
`integrations.transports` and `clients/js/test/transports.steps.mjs` for
`clients/js/transports.js`. Each is steps on one transport, in order:

- `cassette` is a cassette (`integrations.cassette/1`, or the old `responses` format) the
  steps replay from; or `record` is the provider's answers in turn (each a cassette answer,
  with its `elapsed_ms`), recorded by a `Recorder` with the vector's `scenario`; `faults`
  wraps either in `Faulty`, whose clock is counted as `slept`.
- A step is `{"call": {...}}` (plugin, operation, method, url, headers, body as
  `json`/`text`/`base64`/`{"repeat", "times"}`; `{{N.key}}` in it is the value at `key` of
  result N), `save` and `promote` (the file, less `recorded`, and which of `absent`'s texts
  leaked into it), `replay` (a Cassette of what was saved), `unused`, `close`, or `rules`
  (a plugin's record rules).
- `expect.results` has one entry a step: an answer (`status`, `headers` in lower case and
  the body) or `{"error": ...}`; a refusal is its class and which of the expected
  `mentions` its message holds.
