Feature: The vectors in tests/vectors/, which tests/steps/test_vectors.py runs on calls.py too
  Each vector is one call: what it is given, what the providers answer, and what the
  caller must come to (its "expect"), the same for caller.js and calls.py. Each in
  tests/vectors/cassettes/ is steps on a Cassette, a Recorder or Faulty, the same for
  transports.js and transports.py.

  Scenario Outline: caller.js gives the vector what it expects
    Given the call in "tests/vectors/<vector>"
    When caller.js makes it
    Then the result is the JSON at "expect" in "tests/vectors/<vector>"

    Examples:
      | vector |
      | advisory.json |
      | event-four-tries.json |
      | jitter.json |
      | no-answer.json |
      | ok.json |
      | person-waits.json |
      | post-once.json |
      | post-repeatable.json |
      | refusal.json |
      | refused-in-body.json |
      | request-id.json |
      | retry-after-capped.json |
      | retry-after-elsewhere.json |
      | retry-after.json |
      | scheduled-backoff.json |
      | scheduled-exhausted.json |
      | too-many.json |
      | unicode.json |
      | unreadable-body.json |

  Scenario: Every vector in tests/vectors/ is run, and there are vectors to run
    When I list the vectors in "tests/vectors"
    Then the result is:
      """
      ["advisory.json", "event-four-tries.json", "jitter.json", "no-answer.json", "ok.json", "person-waits.json", "post-once.json", "post-repeatable.json", "refusal.json", "refused-in-body.json", "request-id.json", "retry-after-capped.json", "retry-after-elsewhere.json", "retry-after.json", "scheduled-backoff.json", "scheduled-exhausted.json", "too-many.json", "unicode.json", "unreadable-body.json"]
      """

  Scenario Outline: transports.js gives the cassette vector what it expects: <vector>
    Given a scratch directory
    And the cassette vector "<vector>"
    When the transports run it
    Then the result is the JSON at "expect" in "tests/vectors/cassettes/<vector>"

    Examples:
      | vector |
      | baseline.json |
      | bodies.json |
      | digest.json |
      | escape.json |
      | faulty.json |
      | graphql.json |
      | host.json |
      | in-order.json |
      | labels.json |
      | loose.json |
      | match.json |
      | names.json |
      | no-snapshot.json |
      | old-format.json |
      | repeats.json |
      | rules.json |
      | secret-leak.json |
      | secret.json |
      | unused.json |
      | volatile.json |

  Scenario: Every vector in tests/vectors/cassettes/ is run
    When I list the vectors in "tests/vectors/cassettes"
    Then the result is:
      """
      ["baseline.json", "bodies.json", "digest.json", "escape.json", "faulty.json", "graphql.json", "host.json", "in-order.json", "labels.json", "loose.json", "match.json", "names.json", "no-snapshot.json", "old-format.json", "repeats.json", "rules.json", "secret-leak.json", "secret.json", "unused.json", "volatile.json"]
      """
