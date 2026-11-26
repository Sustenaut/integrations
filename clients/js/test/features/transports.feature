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

  Scenario: A recording is not saved outside its cassettes directory
    Given a scratch directory
    And the environment does not set "INTEGRATIONS_CASSETTES"
    And the environment does not set "INTEGRATIONS_CASSETTE"
    When a recorder made for "cassettes/c.json" is saved as "cassettes/../outside.json"
    Then it is refused, saying "outside the cassettes directory"
    And the refusal's "name" is "CassettePathError"
    And the file "outside.json" does not exist

  Scenario: Under INTEGRATIONS_CASSETTES, a recording climbing out of it is not saved
    Given a scratch directory
    And the environment sets "INTEGRATIONS_CASSETTES" to "cassettes" in the scratch directory
    When a recorder made for "cassettes/../outside.json" is saved
    Then it is refused, saying "outside the cassettes directory"
    And the file "outside.json" does not exist

  Scenario: A recording is not saved through a symlink out of its directory
    Given a scratch directory
    And the environment does not set "INTEGRATIONS_CASSETTES"
    And the environment does not set "INTEGRATIONS_CASSETTE"
    And a file "outside.json" holding "{}"
    And "cassettes/c.json" is a symlink to "../outside.json"
    When a recorder made for "cassettes/c.json" is saved
    Then it is refused, saying "outside the cassettes directory"
    And the file "outside.json" holds "{}"

  Scenario: A recording is not saved through a dangling symlink out of its directory
    Given a scratch directory
    And the environment does not set "INTEGRATIONS_CASSETTES"
    And the environment does not set "INTEGRATIONS_CASSETTE"
    And "cassettes/c.json" is a symlink to "../outside.json"
    When a recorder made for "cassettes/c.json" is saved
    Then it is refused, saying "outside the cassettes directory"
    And the file "outside.json" does not exist

  Scenario: A cassette that is a symlink out of its directory is not promoted
    Given a scratch directory
    And the environment does not set "INTEGRATIONS_CASSETTES"
    And the environment does not set "INTEGRATIONS_CASSETTE"
    And a file "outside.json" holding "{}"
    And "cassettes/c.json" is a symlink to "../outside.json"
    When I promote "cassettes/c.json"
    Then it is refused, saying "outside the cassettes directory"
    And the file "outside.json" holds "{}"

  Scenario: A recording inside its cassettes directory is saved
    Given a scratch directory
    And the environment sets "INTEGRATIONS_CASSETTES" to "cassettes" in the scratch directory
    When a recorder made for "cassettes/deep/c.json" is saved
    Then it is not refused
    And the JSON in "cassettes/deep/c.json" has "format" equal to "integrations.cassette/1"

  Scenario Outline: <mode> mode refuses an INTEGRATIONS_CASSETTE outside INTEGRATIONS_CASSETTES
    Given a scratch directory
    And a file "outside.json" holding:
      """
      {"responses": {}}
      """
    And the environment sets "INTEGRATIONS_MODE" to "<mode>"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTES" to "cassettes" in the scratch directory
    And the environment sets "INTEGRATIONS_CASSETTE" to "cassettes/../outside.json" in the scratch directory
    When I ask for the current transport
    Then it is refused, saying "outside the cassettes directory"

    Examples:
      | mode   |
      | replay |
      | record |

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

  Scenario: A process under INTEGRATIONS_FAULTS fails as its harness says, on every plugin under a name
    Given a scratch directory
    And a file "c.json" holding the cassette of "tests/vectors/cassettes/match.json"
    And the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json" in the scratch directory
    And the environment sets "INTEGRATIONS_FAULTS" to "[{\"match\": \"github GET /repos/*/pulls\", \"effect\": \"status 418\", \"body\": {\"message\": \"no\"}}]"
    When a caller given no fetch calls "github.board-read" "read" with "GET https://api.github.com/repos/o/r/pulls?state=open&per_page=100"
    Then the result is:
      """
      {"status": 418, "json": {"message": "no"}}
      """
    And the calls recorded are:
      | plugin            | outcome | status |
      | github.board-read | refused | 418    |

  Scenario: Under INTEGRATIONS_FAULTS the current transport is Faulty
    Given a scratch directory
    And a file "c.json" holding "{\"responses\": {}}"
    And the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json" in the scratch directory
    And the environment sets "INTEGRATIONS_FAULTS" to "[{\"match\": \"*\", \"effect\": \"no-answer\"}]"
    When I ask for the current transport
    Then the result is "Faulty"

  Scenario: INTEGRATIONS_FAULTS without PUBTEST_RUN does not start
    Given the environment does not set "INTEGRATIONS_MODE"
    And the environment does not set "PUBTEST_RUN"
    And the environment sets "INTEGRATIONS_FAULTS" to "[{\"match\": \"*\", \"effect\": \"no-answer\"}]"
    When I ask for the current transport
    Then it is refused, saying "INTEGRATIONS_FAULTS without PUBTEST_RUN: only a test harness makes calls fail on purpose, so this process does not start"
    And the refusal's "name" is "ModeError"

  Scenario Outline: INTEGRATIONS_FAULTS that is no list of rules is refused: <given>
    Given the environment does not set "INTEGRATIONS_MODE"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_FAULTS" to "<given>"
    When I ask for the current transport
    Then it is refused, saying "<saying>"

    Examples:
      | given                    | saying                                                                     |
      | {\"effect\": \"no-answer\"}  | INTEGRATIONS_FAULTS is a JSON list of Faulty's rules, each with an effect |
      | [{\"match\": \"*\"}]           | INTEGRATIONS_FAULTS is a JSON list of Faulty's rules, each with an effect |
      | [{\"effect\": \"explode\"}]    | no fault \"explode\"                                                         |

  Scenario: A process reports what its cassette answered, as a harness checks at exit
    Given a scratch directory
    And a file "reported.json" holding the cassette of "tests/vectors/cassettes/match.json"
    And the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "reported.json" in the scratch directory
    When a caller given no fetch calls "github.board-read" "read" with "GET https://api.github.com/repos/o/r/pulls?state=open&per_page=100", and the process reports its cassette
    Then the result is:
      """
      {"calls": 1, "used": [0], "sent": 1, "answered": 1, "named": true}
      """

  Scenario: A harness's clock, sleep and jitter reach a caller made before them
    When a caller made before the harness's clock calls "github.board-read" "read" with "GET https://api.github.com/x", answered 503 and then 200
    Then the result is:
      """
      {"status": 200, "slept": [0.25], "latency_ms": 250}
      """

  Scenario: A caller given its own sleep keeps it under a harness's clock
    When a caller with its own sleep, made before the harness's clock, calls "github.board-read" "read" with "GET https://api.github.com/x", answered 503 and then 200
    Then the result's "slept" is "[]"
    And the result's "own" is "[0.25]"
