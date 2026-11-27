Feature: pubtest's replay of calls to outside services, broken on purpose
  Each scenario runs a small suite of its own with cassettes on (its own
  pubrules.json, "cassettes": "cassettes"), as harness-recording.feature does.
  Its step `the inner client gets "URL"` calls github.repo-ops' "call"
  operation, which is tried up to four times, through a network that answers
  {"name": the URL's last part}. `pubtest record` records the scenario a fault
  scenario borrows; then the suite replays, with pubtest's fault steps put on
  each scenario's transport, over its cassette.

  Background:
    Given a scratch directory
    And the program under test is "pubtest"
    And a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And a file "cassettes/README" holding "recorded answers"

  Scenario: A recorded call is answered as it was recorded
    Given an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
          And "github.repo-ops" was called 1 times
          And "github" was called 1 times
          And "slack" was called 0 times
          And the Caller slept 0 seconds
      """
    When I run "pubtest record --all"
    And the inner suite runs
    Then it succeeds

  Scenario: An integration that can't be reached is tried again, waiting between tries
    Given an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Can't reach GitHub
          Given the "github" integration can't be reached
          When the inner client gets "https://api.github.com/repos/o/r"
          Then it is refused, saying "no answer"
          And "github.repo-ops" was called 4 times
          And the Caller slept 3.5 seconds
      """
    When the inner suite runs
    Then it succeeds

  Scenario: An integration that does not answer in time is no answer
    Given an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: GitHub is slow
          Given the "github.repo-ops" integration does not answer in time
          When the inner client gets "https://api.github.com/repos/o/r"
          Then it is refused, saying "timed out"
          And "github.repo-ops" was called 4 times
      """
    When the inner suite runs
    Then it succeeds

  Scenario: An answer the fault gives is the code's to read
    Given an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gone
          Given the "github" integration answers "GET /repos/*/gone" with 404 and:
            ```
            {"message": "Not Found"}
            ```
          When the inner client gets "https://api.github.com/repos/o/gone"
          Then the result's "message" is "Not Found"
          And "github" was called 1 times
      """
    When the inner suite runs
    Then it succeeds

  Scenario: After a failed try, the recorded answer
    Given an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"

        Scenario: Gets it after a 503
          Given the cassette of "Gets a repository"
          And the "github" integration answers with 503, then as recorded
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
          And "github.repo-ops" was called 2 times
          And the Caller slept 0.5 seconds
      """
    When I run "pubtest record \"demo.feature: Gets a repository\""
    And the inner suite runs
    Then it succeeds

  Scenario: An answer that is not JSON is refused
    Given an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"

        @cassette:demo/gets-a-repository
        Scenario: Gets what is not JSON
          Given the "github" integration answers with what is not JSON
          When the inner client gets "https://api.github.com/repos/o/r"
          Then it is refused
      """
    When I run "pubtest record \"demo.feature: Gets a repository\""
    And the inner suite runs
    Then it succeeds

  Scenario: A test customer's names are the ones it was recorded with
    Given an inner suite with the example test customer and the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets the customer's repository
          Given the example customer's repository "the repository"
          When the inner client gets the example customer's repository "the repository"
          Then the result's "name" is "pubtest-example-1"
      """
    When I run "pubtest record --all"
    And the inner suite runs
    Then it succeeds
    And the file "cassettes/demo/gets-the-customer-s-repository.json" includes "pubtest-example-1"
