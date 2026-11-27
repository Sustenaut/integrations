Feature: Recording cassettes, and what replay refuses
  Each scenario here runs a small suite of its own, in the runner's language:
  its feature is given here, and its one step of its own, `the inner client
  gets "URL"`, calls github.repo-ops through a Caller, whose network answers
  {"name": the URL's last part}. Its program, inner-client, makes the same
  call. pubtest record runs that suite, as it would a repository's.

  Background:
    Given a scratch directory
    And the program under test is "pubtest"

  Scenario: A replay with no data repository fails at the start
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": true}}
      """
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When the inner suite runs
    Then it fails
    And the inner run says "no data repo: `pubtest record --all` creates it"

  Scenario: Recording with no data repository says how to create it
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": true}}
      """
    When I run "pubtest record --all"
    Then it fails
    And stderr has a line matching "  (cd * && gh repo create OWNER/*-data --private --clone --description *)"
    And stderr mentions "--create-data-repo"

  Scenario: Asked to, it creates the data repository with gh
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": true}}
      """
    And a stand-in "gh"
    When I run "pubtest record --all --create-data-repo"
    Then it fails
    And "gh" was run with arguments matching "repo create OWNER/*-data --private --clone --description *"
    And stderr mentions "gh repo create failed"

  Scenario: A scenario with no cassette fails, saying how to record it
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And a file "cassettes/README" holding "recorded answers"
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When the inner suite runs
    Then it fails
    And the inner run says "no cassette for demo.feature: Gets a repository: record it"

  Scenario: A recording is the cassette the replay answers from
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When I run "pubtest record --all"
    Then it succeeds
    And it prints "recorded demo/gets-a-repository.json"
    And stderr does not mention "record transport is a stand-in"
    And the JSON in "cassettes/demo/gets-a-repository.json" has "scenario" equal to "demo.feature: Gets a repository"
    And the JSON in "cassettes/demo/gets-a-repository.json" has "current.calls.0.plugin" equal to "github.repo-ops"
    And the cassette "cassettes/demo/gets-a-repository.json" was recorded with this integrations
    And the file "cassettes/demo/gets-a-repository.json" includes "⟦integrations:date:1⟧"
    When the inner suite runs
    Then it succeeds

  Scenario: Each Examples row has a cassette of its own
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario Outline: Gets the repository <name>
          When the inner client gets "https://api.github.com/repos/o/<name>"
          Then the result's "name" is "<name>"

          Examples:
            | name |
            | r    |
            | s    |
      """
    When I run "pubtest record --all"
    Then it succeeds
    And the JSON in "cassettes/demo/gets-the-repository-name/2.json" has "scenario" equal to "demo.feature: Gets the repository <name> #2"
    And the JSON in "cassettes/demo/gets-the-repository-name/2.json" has "current.calls.0.response.body.json.name" equal to "s"

  Scenario: A recorded call no process makes any more fails the scenario
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When I run "pubtest record --all"
    And the file "features/demo.feature" changes to:
      """
      Feature: Demo
        Scenario: Gets a repository
          When I run "true"
          Then it succeeds
      """
    And the inner suite runs
    Then it fails
    And the inner run says "1 recorded call in demo.feature: Gets a repository never made"

  Scenario: A program the scenario runs answers from its cassette, and says which calls it used
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When I run "pubtest record --all"
    And the file "features/demo.feature" changes to:
      """
      Feature: Demo
        Scenario: Gets a repository
          When I run "inner-client https://api.github.com/repos/o/r"
          Then the JSON it prints has "name" equal to "r"
      """
    And the inner suite runs
    Then it succeeds

  Scenario: --changed records only the scenarios whose cassette is out of date
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"

        Scenario: Gets another repository
          When the inner client gets "https://api.github.com/repos/o/s"
          Then the result's "name" is "s"
      """
    When I run "pubtest record --all"
    And the file "features/demo.feature" changes to:
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"

        Scenario: Gets another repository
          When the inner client gets "https://api.github.com/repos/o/t"
          Then the result's "name" is "t"
      """
    And I run "pubtest record --changed"
    Then it succeeds
    And it prints "demo.feature: Gets another repository: its text changed"
    And it does not print "demo.feature: Gets a repository:"
    And it prints "recorded demo/gets-another-repository.json"
    And it does not print "recorded demo/gets-a-repository.json"
    And the file "cassettes/demo/gets-another-repository.json" includes "/repos/o/t"

  Scenario: --rename moves a scenario's cassette to its new title
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When I run "pubtest record --all"
    And I run "pubtest record --rename \"demo.feature: Gets a repository\" \"Fetches a repository\""
    Then it succeeds
    And the file "cassettes/demo/gets-a-repository.json" does not exist
    And the JSON in "cassettes/demo/fetches-a-repository.json" has "scenario" equal to "demo.feature: Fetches a repository"

  Scenario: --rename finds a recorded scenario by its title alone
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When I run "pubtest record --all"
    And I run "pubtest record --rename \"Gets a repository\" \"Fetches a repository\""
    Then it succeeds
    And it prints "demo/gets-a-repository.json -> demo/fetches-a-repository.json"

  Scenario: Recording the real network into a directory of the repository's own warns
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite on the real network with the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Calls nothing
          When I run "true"
          Then it succeeds
      """
    When I run "pubtest record --all"
    Then it succeeds
    And stderr mentions "which is only for a suite whose record transport is a stand-in"

  Scenario: A test customer's cleanup runs after the cassette is saved, with the scenario's cassette still set
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with a watched test customer and the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          Given the watched customer's repository "the repository"
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When I run "pubtest record --all"
    Then it succeeds
    And the file "cleanups.txt" holds:
      """
      the scenario's cleanup, in record mode, with the scenario's cassette, after it passed
      the customer's cleanup of watched-1, in record mode, with the scenario's cassette, after it passed
      after the scenarios, INTEGRATIONS_CASSETTE is unset
      """
    And the file "cassettes/demo/gets-a-repository.json" includes "/repos/o/r"
    And the file "cassettes/demo/gets-a-repository.json" does not include "/repos/o/watched-1"

  Scenario: In replay the scenario's own cleanups see its cassette, and no customer's cleanup runs
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with a watched test customer and the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          Given the watched customer's repository "the repository"
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When I run "pubtest record --all"
    And the inner suite runs
    Then it succeeds
    And the file "cleanups.txt" holds:
      """
      the scenario's cleanup, in record mode, with the scenario's cassette, after it passed
      the customer's cleanup of watched-1, in record mode, with the scenario's cassette, after it passed
      after the scenarios, INTEGRATIONS_CASSETTE is unset
      the scenario's cleanup, in replay mode, with the scenario's cassette, after it passed
      after the scenarios, INTEGRATIONS_CASSETTE is unset
      """

  Scenario: A failed scenario's cleanups run too, with its cassette set
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with a watched test customer and the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          Given the watched customer's repository "the repository"
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "s"
      """
    When I run "pubtest record --all"
    Then it fails
    And the file "cleanups.txt" holds:
      """
      the scenario's cleanup, in record mode, with the scenario's cassette, after it failed
      the customer's cleanup of watched-1, in record mode, with the scenario's cassette, after it failed
      after the scenarios, INTEGRATIONS_CASSETTE is unset
      """
    And the file "cassettes/demo/gets-a-repository.json" does not exist

  Scenario: A test customer's failed cleanup fails a scenario that passed
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with a watched test customer and the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          Given the watched customer's repository "the repository"
          And the watched customer's cleanup fails
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "r"
      """
    When I run "pubtest record --all"
    Then it fails
    And the inner run says "the test customer inner.watched's cleanup failed: the watched cleanup broke"
    And the file "cleanups.txt" includes "after the scenarios, INTEGRATIONS_CASSETTE is unset"

  Scenario: A failed cleanup does not hide the scenario's own failure
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    And an inner suite with a watched test customer and the feature "demo.feature":
      """
      Feature: Demo
        Scenario: Gets a repository
          Given the watched customer's repository "the repository"
          And the watched customer's cleanup fails
          When the inner client gets "https://api.github.com/repos/o/r"
          Then the result's "name" is "s"
      """
    When I run "pubtest record --all"
    Then it fails
    And the inner run says "the test customer inner.watched's cleanup failed: the watched cleanup broke"
    And the inner run says "the result's name is \"r\", not s"

  Scenario Outline: What pubtest record refuses
    Given a file "pubrules.json" holding:
      """
      {"pubtest": {"cassettes": "cassettes"}}
      """
    When I run "pubtest <arguments>"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | arguments                                      | message                                          |
      | record                                         | say which: --all, --changed, or the scenarios    |
      | record --all 'demo.feature: Gets a repository' | --all and --changed take no scenarios            |
      | record --rename 'demo.feature: A' B --all      | --rename moves one cassette, and records nothing |
      | record --rename 'demo.feature: A' B            | demo.feature: A has no cassette                  |
      | record --rename 'Gets a repository' B          | names 0 recorded scenarios                       |
      | replay                                         | no command                                       |
