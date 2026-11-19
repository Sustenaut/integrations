Feature: Record and replay in JavaScript: the transport a Caller given no fetch uses, and caller.fetch
  transports.js, as src/integrations/transports.py; the cassette vectors run in
  vectors.feature. A test harness installs a transport with use(); otherwise
  INTEGRATIONS_MODE picks it, and only under PUBTEST_RUN may it be other than live.

  Scenario: With no mode, the current transport is the network's fetch
    Given the environment does not set "INTEGRATIONS_MODE"
    When I ask for the current transport
    Then the result is "live"

  Scenario Outline: A <mode> mode without PUBTEST_RUN does not start
    Given the environment sets "INTEGRATIONS_MODE" to "<mode>"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment does not set "PUBTEST_RUN"
    When I ask for the current transport
    Then it is refused, saying "INTEGRATIONS_MODE=<mode> without PUBTEST_RUN"
    And the refusal's "name" is "ModeError"

    Examples:
      | mode   |
      | replay |
      | record |

  Scenario: A mode that is not one is refused
    Given the environment sets "INTEGRATIONS_MODE" to "replaying"
    When I check the mode
    Then it is refused, saying "INTEGRATIONS_MODE is 'replaying': it is live, replay or record"

  Scenario: A mode other than live needs its cassette
    Given the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment does not set "INTEGRATIONS_CASSETTE"
    When I check the mode
    Then it is refused, saying "needs INTEGRATIONS_CASSETTE"

  Scenario: Record mode records what the network answers
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "recorded.json"
    When I ask for the current transport
    Then the result is "Recorder"

  Scenario: A caller given no fetch calls through the transport a harness uses
    Given the environment does not set "INTEGRATIONS_MODE"
    And a cassette from "tests/vectors/cassettes/match.json"
    When a caller given no fetch calls "github.board-read" "read" with "GET https://api.github.com/repos/o/r/pulls?state=open&per_page=100" while the cassette is in use
    Then the result is:
      """
      [{"number": 7, "title": "A"}]
      """
    And the calls recorded are:
      | plugin            | operation | outcome | status |
      | github.board-read | read      | ok      | 200    |

  Scenario: Once the harness puts its transport back, the network's is current again
    Given the environment does not set "INTEGRATIONS_MODE"
    And a cassette from "tests/vectors/cassettes/match.json"
    When I ask for the current transport after the cassette is used and put back
    Then the result is "live"

  Scenario: A call with no recording is refused as that, not retried as no answer
    Given the environment does not set "INTEGRATIONS_MODE"
    And a cassette from "tests/vectors/cassettes/match.json"
    When a caller given no fetch calls "github.board-read" "read" with "GET https://api.github.com/repos/o/r" while the cassette is in use
    Then it is refused, saying "record it: pubtest record \"vectors: match\""
    And the refusal's "name" is "NoSnapshot"
    And no call was recorded

  Scenario: Code that takes a fetch works through caller.fetch, each request a call
    Given a cassette from "tests/vectors/cassettes/no-snapshot.json"
    When code that takes a fetch opens a pull request at "https://api.github.com/repos/o/r/pulls" through caller.fetch "github.edit-pr" "open-pr"
    Then the result's "status" is "201"
    And the result's "json.number" is "8"
    And the calls recorded are:
      | plugin         | operation | outcome | status | bytes_out |
      | github.edit-pr | open-pr   | ok      | 201    | 38        |

  Scenario: caller.fetch with no answer throws a TypeError, as fetch does
    When code that takes a fetch opens a pull request at "https://api.github.com/repos/o/r/pulls" through caller.fetch "github.edit-pr" "open-pr", with no answer
    Then it is refused, saying "fetch failed: github.edit-pr open-pr: no answer: down"
    And the refusal's "name" is "TypeError"
    And the calls recorded are:
      | outcome     |
      | unavailable |

  Scenario: caller.fetch of an operation not in the registry is refused when it is made
    When I make a fetch of "github.edit-pr" "nope" from a caller
    Then it is refused, saying "github.edit-pr nope is not a registered operation"
