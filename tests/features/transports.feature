Feature: Record and replay: the transport a caller given none uses, cassettes in a child process, and Caller.opener
  integrations.transports; the cassette vectors (tests/vectors/cassettes/) run in
  vectors.feature, and in clients/js too. A test harness installs a transport with
  use(); otherwise INTEGRATIONS_MODE picks it, and only under PUBTEST_RUN may it be
  other than live.

  Scenario: With no mode, a caller given no transport goes to the network
    Given the environment does not set "INTEGRATIONS_MODE"
    When I ask for the current transport
    Then the result is "Network"

  Scenario Outline: A <mode> mode without PUBTEST_RUN does not start
    Given the environment sets "INTEGRATIONS_MODE" to "<mode>"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment does not set "PUBTEST_RUN"
    When I ask for the current transport
    Then it is refused, saying "INTEGRATIONS_MODE=<mode> without PUBTEST_RUN: only a test harness answers from or records cassettes, so this process does not start"

    Examples:
      | mode   |
      | replay |
      | record |

  Scenario: A caller given its own transport does not start under that mode either
    Given the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment does not set "PUBTEST_RUN"
    When a caller given its own transport calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then it is refused, saying "without PUBTEST_RUN"
    And no call was recorded

  Scenario: A mode that is not one is refused
    Given the environment sets "INTEGRATIONS_MODE" to "replaying"
    When I ask for the current transport
    Then it is refused, saying "INTEGRATIONS_MODE is 'replaying': it is live, replay or record"

  Scenario: A mode other than live needs its cassette
    Given the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment does not set "INTEGRATIONS_CASSETTE"
    When I ask for the current transport
    Then it is refused, saying "INTEGRATIONS_MODE=replay needs INTEGRATIONS_CASSETTE, the cassette's file"

  Scenario: Replay mode answers from INTEGRATIONS_CASSETTE
    Given a scratch directory
    And a file "c.json" holding "{"responses": {}}"
    And the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    When I ask for the current transport
    Then the result is "Cassette"

  Scenario: Record mode records what the network answers into INTEGRATIONS_CASSETTE
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "recorded.json"
    When I ask for the current transport
    Then the result is "Recorder around Network"

  Scenario: A child process learns its cassette from INTEGRATIONS_CASSETTE, and answers from it
    Given a scratch directory
    And the program under test is "integrations"
    And a file "slack.json" holding:
      """
      {"format": "integrations.cassette/1", "scenario": "transports.feature: a child process",
       "current": {"recorded": {"at": "2026-11-19T09:00:00Z", "integrations": "0.16.0", "consumer": null}, "volatile": {},
                   "calls": [{"kind": "http", "plugin": "slack.post", "operation": "post",
                              "request": {"method": "POST", "url": "https://slack.com/api/chat.postMessage", "headers": {}, "body": {"text": "channel=U1&text=hi"}},
                              "response": {"status": 200, "headers": {}, "body": {"json": {"ok": true}}}, "elapsed_ms": 90}]},
       "baseline": null}
      """
    And the environment sets "SLACK_TOKEN" to "xoxb-not-sent"
    And the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "slack.json"
    When I run "integrations slack post --channel U1 --text hi"
    Then it succeeds
    And stderr does not mention "Traceback"

  Scenario: A child process's call with no recording fails, saying how to record it
    Given a scratch directory
    And the program under test is "integrations"
    And a file "slack.json" holding:
      """
      {"format": "integrations.cassette/1", "scenario": "transports.feature: a child process", "current": {"volatile": {}, "calls": []}, "baseline": null}
      """
    And the environment sets "SLACK_TOKEN" to "xoxb-not-sent"
    And the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "PUBTEST_RUN" to "1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "slack.json"
    When I run "integrations slack post --channel U1 --text hi"
    Then it fails
    And stderr mentions "no recorded call in transports.feature: a child process for slack.post post POST https://slack.com/api/chat.postMessage"
    And stderr mentions "record it: pubtest record "transports.feature: a child process""

  Scenario: A child process in replay mode without PUBTEST_RUN does not start
    Given a scratch directory
    And the program under test is "integrations"
    And the environment sets "SLACK_TOKEN" to "xoxb-not-sent"
    And the environment sets "INTEGRATIONS_MODE" to "replay"
    And the environment sets "INTEGRATIONS_CASSETTE" to "slack.json"
    And the environment does not set "PUBTEST_RUN"
    When I run "integrations slack post --channel U1 --text hi"
    Then it fails
    And stderr mentions "without PUBTEST_RUN"

  Scenario: A caller given no transport calls through the one a harness uses
    Given the environment does not set "INTEGRATIONS_MODE"
    And a cassette from "tests/vectors/cassettes/match.json"
    When a caller given no transport calls "github.board-read" "read" with "GET https://api.github.com/repos/o/r/pulls?state=open&per_page=100" while the cassette is in use
    Then the result is:
      """
      [{"number": 7, "title": "A"}]
      """
    And the calls recorded are:
      | plugin            | operation | outcome | status |
      | github.board-read | read      | ok      | 200    |

  Scenario: Once the harness's block ends, the network is current again
    Given the environment does not set "INTEGRATIONS_MODE"
    And a cassette from "tests/vectors/cassettes/match.json"
    When I ask for the current transport after the cassette's block
    Then the result is "Network"

  Scenario: A call with no recording is refused as that, not retried as no answer
    Given the environment does not set "INTEGRATIONS_MODE"
    And a cassette from "tests/vectors/cassettes/match.json"
    When a caller given no transport calls "github.board-read" "read" with "GET https://api.github.com/repos/o/r" while the cassette is in use
    Then it is refused, saying "record it: pubtest record "vectors: match""
    And no call was recorded

  Scenario: Code that takes an opener works through Caller.opener, each request a call
    Given a cassette from "tests/vectors/cassettes/no-snapshot.json"
    When code that takes an opener opens a pull request at "https://api.github.com/repos/o/r/pulls" through the caller's opener for "github.edit-pr" "open-pr"
    Then the result's "status" is "201"
    And the result's "json.number" is "8"
    And the calls recorded are:
      | plugin         | operation | outcome | status | bytes_out |
      | github.edit-pr | open-pr   | ok      | 201    | 38        |

  Scenario: An opener's call with no answer raises Unavailable
    Given a cassette from "tests/vectors/cassettes/no-snapshot.json"
    And the cassette gives no answer to "github.edit-pr open-pr"
    When code that takes an opener opens a pull request at "https://api.github.com/repos/o/r/pulls" through the caller's opener for "github.edit-pr" "open-pr"
    Then it is refused, saying "github.edit-pr open-pr: no answer: fault injected"
    And the calls recorded are:
      | outcome     |
      | unavailable |

  Scenario: An opener for an operation not in the registry is refused when it is made
    When I make an opener for "github.edit-pr" "nope"
    Then it is refused, saying "github.edit-pr nope is not a registered operation"

  Scenario: A recording with nowhere to go is not saved
    When I save a recorder that was given no path
    Then it is refused, saying "a recording needs a path to be saved to"

  Scenario: A cassette in record mode only warns of calls never made
    Given a cassette in record mode from "tests/vectors/cassettes/unused.json"
    When I close the cassette
    Then it is not refused
    And the result includes "never made"

  Scenario: A file that is no cassette is refused
    Given a scratch directory
    And a file "c.json" holding "{"calls": []}"
    When I read "c.json" as a cassette
    Then it is refused, saying "c is not a cassette: no format integrations.cassette/1 and no responses"

  Scenario: Accepting a recording makes it the baseline
    Given a scratch directory
    And a file "c.json" copied from "tests/vectors/cassettes/no-snapshot.json"
    When I promote the cassette at "cassette" in "c.json"
    Then the JSON in "c.json" has "baseline.calls.0.request.body.json.title" equal to "A"
    And the JSON in "c.json" has "current.calls.0.request.body.json.title" equal to "A"

  Scenario: Only a cassette can be promoted
    Given a scratch directory
    And a file "c.json" copied from "tests/vectors/cassettes/old-format.json"
    When I promote the cassette at "cassette" in "c.json"
    Then it is refused, saying "is not an integrations.cassette/1 cassette"

  Scenario: GitHub's test steps register a repository in replay
    Given the environment does not set "INTEGRATIONS_MODE"
    And the GitHub repository "o/r" holds:
      """
      {"README.md": "# r"}
      """
    When I list the scenario's GitHub repositories
    Then the result is:
      """
      {"o/r": {"recorded": "o/r", "files": {"README.md": "# r"}}}
      """

  Scenario: GitHub's test steps cannot create a repository when recording yet
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    When I set up the GitHub repository "o/r" holding "{}"
    Then it is refused, saying "needs the GitHub test org: a later release"

  Scenario Outline: The pytest plugin loads GitHub's steps only when a suite asks: <ini>
    When pytest configures the integrations.testing plugin with integrations_testing <ini>
    Then the result is "<loaded>"

    Examples:
      | ini   | loaded                           |
      | true  | ["integrations.testing.github"]  |
      | false | []                               |
