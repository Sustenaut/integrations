Feature: The vectors in tests/vectors/, which clients/js/caller.js runs too, so both callers call, retry and record the same way

  Scenario Outline: A vector: <description>
    Given the vector "<vector>"
    When the caller makes its call
    Then the result is what the vector expects
    And each call record meets its schema

    Examples:
      | vector | description |
      | advisory.json | The repository's mode reaches the record |
      | event-four-tries.json | An event-driven call (E) is tried four times, 1, 2 and 4 s apart |
      | jitter.json | The wait is random, up to 2**n s: here a quarter of it |
      | no-answer.json | Nothing recorded is no answer: status null |
      | ok.json | A call that worked: one record, its sizes, no retries |
      | person-waits.json | A call a person waits on (H) is tried once |
      | post-once.json | A POST is not safe to repeat, so it is tried once |
      | post-repeatable.json | A POST that says it is repeatable is retried |
      | refusal.json | A refusal comes back to the client, is recorded, and isn't retried |
      | refused-in-body.json | A provider that says no in a 200: refused |
      | request-id.json | The app's own request id goes into the record |
      | retry-after-capped.json | A 429's Retry-After is waited for up to the class's timeout (S: 30 s) |
      | retry-after-elsewhere.json | Retry-After counts on 429 and 503 only, and only when it says a time |
      | retry-after.json | A 503's Retry-After is how long to wait |
      | scheduled-backoff.json | A scheduled call (S) is tried again after 1 s and 2 s, past no answer |
      | scheduled-exhausted.json | Three tries, then Unavailable with the last status |
      | too-many.json | 429 is a failure, not a refusal |
      | unicode.json | A body outside ASCII counts as json.dumps writes it |
      | unreadable-body.json | A body that can't be read isn't a refusal: the client deals with it |

  Scenario: Every vector is run above
    When I list the vectors of calls
    Then the result is:
      """
      ["advisory.json", "event-four-tries.json", "jitter.json", "no-answer.json", "ok.json", "person-waits.json", "post-once.json", "post-repeatable.json", "refusal.json", "refused-in-body.json", "request-id.json", "retry-after-capped.json", "retry-after-elsewhere.json", "retry-after.json", "scheduled-backoff.json", "scheduled-exhausted.json", "too-many.json", "unicode.json", "unreadable-body.json"]
      """

  Scenario: A file the JS sink wrote reads as its records and report
    Given the vector "sink.json"
    When I read "sink.avro", the file the JS sink wrote
    Then the result's "records" is the vector's "records"
    And the result's "report" is the vector's "report"
    And the result's "name" is the vector's "name"
