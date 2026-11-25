Feature: The vectors in tests/vectors/, which clients/js/caller.js runs too, so both callers call, retry and record the same way
  And the cassette vectors in tests/vectors/cassettes/, which clients/js/transports.js runs
  too, so both record, normalise and replay the same way.

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
      | no-answer.json | No answer: status null |
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

  Scenario Outline: A cassette vector: <description>
    Given a scratch directory
    And the cassette vector "<vector>"
    When the transports run it
    Then the result is what the vector expects

    Examples:
      | vector | description |
      | baseline.json | A re-record is the candidate: the previous recording becomes the baseline unless there is one, and only promote moves it |
      | bodies.json | Text, JSON and binary bodies are kept, of requests and answers; binary as base64 |
      | digest.json | A request body over 64 KiB is written as its digest and matched by it |
      | escape.json | Real text holding the placeholders' prefix is written escaped, and replayed as it was |
      | faulty.json | Faulty's effects: no answer, a status, slow (on the test's clock), malformed, then the recorded answers |
      | graphql.json | A GraphQL request matches by its operation's name and variables, however its query is laid out |
      | host.json | The host is part of the match: api.github.com is not uploads.github.com |
      | in-order.json | The same request twice is answered by its recorded calls in order, each once |
      | loose.json | A placeholder first seen in a request binds to the first value the code sends there: answered as that value, and only that value matches it again; a value already another placeholder's binds none |
      | match.json | A request finds its recorded call: its query in any order, its host in any case, its headers aside |
      | names.json | A recording against the test org writes its real names as the scenario's, a whole name at a time, so the cassette and a replay only see o/r |
      | no-snapshot.json | A call with no recording raises NoSnapshot, naming the scenario, the call, the nearest recorded one and how they differ |
      | old-format.json | The old responses format reads as a cassette: answers by METHOD url, a list in turn and its last for good |
      | repeats.json | A request made again is kept again, in order, each with how long it took, and replayed in that order |
      | rules.json | A plugin's record rules, its extends merged over the defaults; a plugin with none gets the defaults |
      | secret-leak.json | A secret the recorder saw that would still be written, here echoed in a body, stops the save |
      | secret.json | A secret is never written: the Authorization header dropped, a secret query parameter and JSON path replaced; replay answers a stand-in token |
      | unused.json | A recorded call never made is listed, and closing the cassette fails on it |
      | volatile.json | Volatile values are written as placeholders in requests and answers, replayed as stand-ins, and a stand-in sent back matches; a value the code sent first (the tree) is answered as it sent it |

  Scenario: Every cassette vector is run above
    When I list the vectors of cassettes
    Then the result is:
      """
      ["baseline.json", "bodies.json", "digest.json", "escape.json", "faulty.json", "graphql.json", "host.json", "in-order.json", "loose.json", "match.json", "names.json", "no-snapshot.json", "old-format.json", "repeats.json", "rules.json", "secret-leak.json", "secret.json", "unused.json", "volatile.json"]
      """

  Scenario: A file the JS sink wrote reads as its records and report
    Given the vector "sink.json"
    When I read "sink.avro", the file the JS sink wrote
    Then the result's "records" is the vector's "records"
    And the result's "report" is the vector's "report"
    And the result's "name" is the vector's "name"
