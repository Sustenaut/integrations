Feature: The vectors in tests/vectors/, which tests/steps/test_vectors.py runs on calls.py too
  Each vector is one call: what it is given, what the providers answer, and what the
  caller must come to (its "expect"), the same for caller.js and calls.py.

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
