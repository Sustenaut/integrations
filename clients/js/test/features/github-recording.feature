Feature: Recording against the GitHub test org in Node: GitHub's test steps, as a test customer
  testing-github.js, as src/integrations/testing/github.py: the customer pubtest's
  register(api, { customers: [github] }) takes. Recording, a repository a scenario
  names is made in the test org under a name of its own, its real names reach the
  Recorder as the scenario's, and pubtest's cleanup of the customer deletes it;
  replaying, the steps only say what the scenario's names are. These run against a
  stand-in for the test org (test/github-standin.mjs), and a stand-in for pubtest's
  tape where a scenario says it runs under the harness.

  Scenario: Recording, the repository holds exactly the scenario's files, in one commit, and is private
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n", "dir/b.txt": "keep\n"}
      """
    When I read the stand-in test org
    Then the result is:
      """
      {"o/r": {"private": true, "description": "A recording's repository: its run deletes it, and the janitor any left after a day",
               "branches": {"main": {"files": {"a.txt": "old\n", "dir/b.txt": "keep\n"}, "commits": 1}}}}
      """

  Scenario: Recording, a repository is made under a name of its own, and its real names go in INTEGRATIONS_NAMES
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And the environment does not set "INTEGRATIONS_NAMES"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the GitHub repository "o/r" holds:
      """
      {}
      """
    When I read INTEGRATIONS_NAMES
    Then the result's "recorded" matches "SustenautTest/rec-*-run1-*"
    And the result's "names" is:
      """
      {"SustenautTest/<r>": "o/r", "<r>": "r", "SustenautTest": "o"}
      """

  Scenario: Recording, the code under test calls the real repository, and the cassette says o/r
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the code under test records its calls
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    When the code under test reads the repository "o/r"
    Then the result's "full_name" matches "SustenautTest/rec-*-run1-*"
    And the recording's answer 0 is:
      """
      {"url": "https://api.github.com/repos/o/r", "full_name": "o/r", "html_url": "https://github.com/o/r", "owner": "o"}
      """

  Scenario: Recording, someone else's push is a real commit, which the code under test meets
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the code under test records its calls
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    And the code under test read "main" of "o/r"
    And someone else pushes to "main" of "o/r":
      """
      {"a.txt": "theirs\n"}
      """
    When the code under test commits "a.txt" as "mine" on what it read of "main" of "o/r"
    Then the result is:
      """
      {"status": 422, "message": "Update is not a fast forward"}
      """

  Scenario: Replaying, the code under test meets the race as Python recorded it, and the steps call nothing
    Given the environment does not set "INTEGRATIONS_MODE"
    And the code under test replays the cassette in "tests/vectors/github/race.json"
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    And the code under test read "main" of "o/r"
    And someone else pushes to "main" of "o/r":
      """
      {"a.txt": "theirs\n"}
      """
    When the code under test commits "a.txt" as "mine" on what it read of "main" of "o/r"
    Then the result is:
      """
      {"status": 422, "message": "Update is not a fast forward"}
      """
    And every call the cassette holds was made

  Scenario: Recording, a push to a branch that does not exist makes it from the default branch, and null deletes a file
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n", "b.txt": "keep\n"}
      """
    And someone else pushes to "feature" of "o/r":
      """
      {"a.txt": null, "c.txt": "new\n"}
      """
    When I read the stand-in test org
    Then the result's "o/r.branches" is:
      """
      {"main": {"files": {"a.txt": "old\n", "b.txt": "keep\n"}, "commits": 1},
       "feature": {"files": {"b.txt": "keep\n", "c.txt": "new\n"}, "commits": 2}}
      """

  Scenario: Replaying, someone else's push is only noted
    Given the environment does not set "INTEGRATIONS_MODE"
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    And someone else pushes to "feature" of "o/r":
      """
      {"a.txt": null}
      """
    When I list the scenario's GitHub repositories
    Then the result is:
      """
      {"o/r": {"recorded": "o/r", "files": {"a.txt": "old\n"}, "pushes": [{"branch": "feature", "files": {"a.txt": null}}]}}
      """

  Scenario: When pubtest cleans up the customer, its repositories are deleted and INTEGRATIONS_NAMES is as it was
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And the environment sets "INTEGRATIONS_NAMES" to "{\"x/y\": \"o/q\"}"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    And the GitHub repository "o/s" holds:
      """
      {}
      """
    When pubtest cleans up the scenario's test customer
    Then the result is:
      """
      {"left": [], "INTEGRATIONS_NAMES": "{\"x/y\": \"o/q\"}", "names": {"github.test": {}}}
      """

  Scenario: Recording, a scenario's repositories share one owner, the test org
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the GitHub repository "o/r" holds:
      """
      {}
      """
    When I set up the GitHub repository "p/s" holding "{}"
    Then it is refused, saying "a scenario's repositories are all in the test org, so they share one owner: \"p/s\" is not \"o/…\""

  Scenario: Recording needs pubtest's harness
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    When I set up the GitHub repository "o/r" holding "{}"
    Then it is refused, saying "recording GitHub repositories needs pubtest's record/replay harness: register(api, { customers: [github] })"

  Scenario: Recording without the recorder app's credentials stops, naming them and ./with-secrets
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment does not set "GITHUB_TEST_RECORDER_TOKEN"
    And the environment does not set "GITHUB_TEST_RECORDER_APP_ID"
    And the environment does not set "GITHUB_TEST_RECORDER_APP_KEY"
    And the environment does not set "GITHUB_TEST_RECORDER_INSTALLATION_ID"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    When I set up the GitHub repository "o/r" holding "{}"
    Then it is refused, saying "the GitHub test org needs the recorder app's credentials, and GITHUB_TEST_RECORDER_APP_ID, GITHUB_TEST_RECORDER_APP_KEY, GITHUB_TEST_RECORDER_INSTALLATION_ID are not set: run it through ./with-secrets"

  Scenario: Recording with the app's id, key and installation id mints its installation token
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment does not set "GITHUB_TEST_RECORDER_TOKEN"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the recorder app's id "123", installation id "456" and a key of its own are in the environment
    And the GitHub repository "o/r" holds:
      """
      {}
      """
    When I ask how the stand-in test org was called
    Then the result is:
      """
      [{"call": "github.app-token mint-in-app POST /app/installations/456/access_tokens", "auth": "a JWT"},
       {"call": "github.repo-ops test-org POST /orgs/SustenautTest/repos", "auth": "Bearer ghs_minted"}]
      """

  Scenario Outline: The token the code under test calls with: <mode>
    Given the environment sets "INTEGRATIONS_MODE" to "<mode>"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the scenario runs under pubtest's harness
    And the GitHub repository "o/r" holds:
      """
      {}
      """
    When I ask for the token the code under test calls GitHub with
    Then the result matches "<token>"

    Examples:
      | mode   | token        |
      | record | ghs_recorder |
      | replay | ghs_0*       |
