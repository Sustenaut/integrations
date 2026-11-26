Feature: Recording against the GitHub test org: GitHub's test steps, and the janitor
  integrations.testing.github. Recording (INTEGRATIONS_MODE=record), a repository a
  scenario names is made in the test org under a name of its own, its real names
  reach the Recorder as the scenario's, and it is deleted when the scenario ends;
  replaying, the steps only say what the scenario's names are, as the cassette holds
  what the code met. These run against a stand-in for the test org
  (steps/github_standin.py), never GitHub. The janitor, `integrations test-org
  sweep`, deletes the rec-* repositories a run left behind.

  Scenario: Recording, a repository is made in the test org under a name of its own
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n", "dir/b.txt": "keep\n"}
      """
    When I ask which name "o/r" was made under
    Then the result matches "SustenautTest/rec-*-run1-*"

  Scenario: Recording, the repository holds exactly the scenario's files, in one commit, and is private
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
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

  Scenario: Recording, a repository that holds nothing is empty
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the GitHub repository "o/r" holds:
      """
      {}
      """
    When I read the stand-in test org
    Then the result's "o/r.branches" is "{}"

  Scenario: Recording, the repository's real names reach the Recorder as the scenario's
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And the environment does not set "INTEGRATIONS_NAMES"
    And a stand-in GitHub test org
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    When I read INTEGRATIONS_NAMES
    Then the result is:
      """
      {"SustenautTest/<r>": "o/r", "<r>": "r", "SustenautTest": "o"}
      """

  Scenario: Recording, the code under test calls the real repository, and the cassette says o/r
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the code under test records its calls
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    When the code under test reads the repository "o/r"
    Then the result's "full_name" matches "SustenautTest/rec-*-run1-*"
    And the recording's calls are:
      | plugin         | operation | method | url                              | status |
      | github.edit-pr | read      | GET    | https://api.github.com/repos/o/r | 200    |
    And the recording's answer 0 has "full_name" equal to "o/r"
    And the recording's answer 0 has "html_url" equal to "https://github.com/o/r"
    And the recording's answer 0 has "owner.login" equal to "o"

  Scenario: Recording, someone else's push is a real commit, which the code under test meets
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
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
    And the recording's calls are:
      | plugin         | operation | method | url                                                                  | status |
      | github.edit-pr | read      | GET    | https://api.github.com/repos/o/r/git/ref/heads/main                  | 200    |
      | github.edit-pr | read      | GET    | https://api.github.com/repos/o/r/git/commits/⟦integrations:sha:1⟧   | 200    |
      | github.edit-pr | open-pr   | POST   | https://api.github.com/repos/o/r/git/trees                           | 201    |
      | github.edit-pr | open-pr   | POST   | https://api.github.com/repos/o/r/git/commits                         | 201    |
      | github.edit-pr | open-pr   | PATCH  | https://api.github.com/repos/o/r/git/refs/heads/main                 | 422    |

  Scenario: Replaying, the code under test meets the race as it was recorded, and the steps call nothing
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

  Scenario: Recording, a push to a branch that does not exist makes it from the default branch, and null deletes a file
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
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

  Scenario: Recording, a push to a repository the scenario did not name is refused
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    When I push to "main" of "o/r" as someone else
    Then it is refused, saying "no GitHub repository "o/r": a step says what it holds first"

  Scenario: Recording, the repository's name says whose run it is, and the next number when one is taken
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "Run_42"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the stand-in test org says the first name it is asked for is taken
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    When I ask how many repositories the stand-in test org was asked to make, and what it holds
    Then the result's "asked" is "2"
    And the result's "holds" is "1"

  Scenario: Recording, a scenario's repositories share one owner, the test org
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the GitHub repository "o/r" holds:
      """
      {}
      """
    When I set up the GitHub repository "p/s" holding "{}"
    Then it is refused, saying "a scenario's repositories are all in the test org, so they share one owner: "p/s" is not "o/…""

  Scenario Outline: A repository is named "owner/name": <name>
    Given the environment does not set "INTEGRATIONS_MODE"
    When I set up the GitHub repository "<name>" holding "{}"
    Then it is refused, saying ""<name>" is no repository: a scenario names one "owner/name", such as "o/r""

    Examples:
      | name  |
      | r     |
      | o/r/x |
      | /r    |

  Scenario: What a repository holds is text
    Given the environment does not set "INTEGRATIONS_MODE"
    When I set up the GitHub repository "o/r" holding "{"a.txt": 1}"
    Then it is refused, saying "o/r's files are a JSON object from each file's path to its text"

  Scenario: When the scenario ends, its repositories are deleted and INTEGRATIONS_NAMES is as it was
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And the environment sets "INTEGRATIONS_NAMES" to "{"x/y": "o/q"}"
    And a stand-in GitHub test org
    And the GitHub repository "o/r" holds:
      """
      {"a.txt": "old\n"}
      """
    And the GitHub repository "o/s" holds:
      """
      {}
      """
    When the scenario's recording ends
    Then the result is:
      """
      {"left": [], "INTEGRATIONS_NAMES": "{\"x/y\": \"o/q\"}"}
      """

  Scenario: A repository whose files could not be written is deleted all the same
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the stand-in test org answers "POST /repos/SustenautTest/*/git/trees" with 422
    When I set up the GitHub repository "o/r" holding "{"a.txt": "old\n"}", and the scenario's recording ends
    Then the result's "refused" includes "writing a tree in SustenautTest/rec-"
    And the result's "left" is "[]"

  Scenario: Recording with the app's id, key and installation id mints its installation token
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment does not set "GITHUB_TEST_RECORDER_TOKEN"
    And a stand-in GitHub test org
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

  Scenario: The token the code under test calls with is the recorder app's when recording
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the GitHub repository "o/r" holds:
      """
      {}
      """
    When I ask for the token the code under test calls GitHub with
    Then the result is "ghs_recorder"

  Scenario: The token the code under test calls with is a stand-in when replaying
    Given the environment does not set "INTEGRATIONS_MODE"
    When I ask for the token the code under test calls GitHub with
    Then the result matches "ghs_*"

  Scenario: Recording without the recorder app's credentials stops, naming them and ./with-secrets
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment does not set "GITHUB_TEST_RECORDER_TOKEN"
    And the environment does not set "GITHUB_TEST_RECORDER_APP_ID"
    And the environment does not set "GITHUB_TEST_RECORDER_APP_KEY"
    And the environment does not set "GITHUB_TEST_RECORDER_INSTALLATION_ID"
    And a stand-in GitHub test org
    When I set up the GitHub repository "o/r" holding "{}"
    Then it is refused, saying "the GitHub test org needs the recorder app's credentials, and GITHUB_TEST_RECORDER_APP_ID, GITHUB_TEST_RECORDER_APP_KEY, GITHUB_TEST_RECORDER_INSTALLATION_ID are not set: run it through ./with-secrets"

  Scenario: The test org is INTEGRATIONS_TEST_ORG when it is set
    Given the environment sets "INTEGRATIONS_MODE" to "record"
    And the environment sets "PUBTEST_RUN" to "run1"
    And the environment sets "INTEGRATIONS_CASSETTE" to "c.json"
    And the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And the environment sets "INTEGRATIONS_TEST_ORG" to "OtherTest"
    And a stand-in GitHub test org "OtherTest"
    And the GitHub repository "o/r" holds:
      """
      {}
      """
    When I ask which name "o/r" was made under
    Then the result matches "OtherTest/rec-*-run1-*"

  Scenario: The janitor deletes the rec-* repositories older than a day, and nothing else
    Given the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And the environment does not set "INTEGRATIONS_TEST_ORG"
    And a stand-in GitHub test org
    And the stand-in test org holds "rec-pubpr-a-1", made 30 hours ago
    And the stand-in test org holds "rec-pubpr-b-1", made 2 hours ago
    And the stand-in test org holds "kept-for-good", made 900 hours ago
    When I run the janitor on the stand-in test org, for repositories older than 24 hours
    Then the result is:
      """
      {"code": 0, "stdout": ["deleted SustenautTest/rec-pubpr-a-1", "SustenautTest: deleted 1 rec-* repositories older than 24 h, kept 1 newer"],
       "stderr": "", "left": ["kept-for-good", "rec-pubpr-b-1"]}
      """

  Scenario: The janitor reads every page of the org's repositories
    Given the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the stand-in test org holds 150 repositories "rec-old-N", made 48 hours ago
    When I run the janitor on the stand-in test org, for repositories older than 24 hours
    Then the result's "left" is "[]"
    And the result's "code" is "0"

  Scenario: The janitor's calls are github.repo-ops' test-org-sweep
    Given the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_recorder"
    And a stand-in GitHub test org
    And the stand-in test org holds "rec-pubpr-a-1", made 30 hours ago
    When I run the janitor on the stand-in test org, for repositories older than 24 hours
    Then the calls recorded are:
      | plugin          | operation      | status |
      | github.repo-ops | test-org-sweep | 200    |
      | github.repo-ops | test-org-sweep | 204    |

  Scenario: The janitor says what GitHub refused
    Given the environment sets "GITHUB_TEST_RECORDER_TOKEN" to "ghs_wrong"
    And a stand-in GitHub test org
    When I run the janitor on the stand-in test org, for repositories older than 24 hours
    Then the result's "code" is "1"
    And the result's "stderr" includes "listing SustenautTest's repositories: HTTP 401: Bad credentials"

  Scenario: The janitor without the recorder app's credentials stops, naming them
    Given the program under test is "integrations"
    And the environment does not set "GITHUB_TEST_RECORDER_TOKEN"
    And the environment does not set "GITHUB_TEST_RECORDER_APP_ID"
    And the environment does not set "GITHUB_TEST_RECORDER_APP_KEY"
    And the environment does not set "GITHUB_TEST_RECORDER_INSTALLATION_ID"
    When I run "integrations test-org sweep"
    Then the exit code is 2
    And stderr mentions "are not set: run it through ./with-secrets, which exports GITHUB_TEST_RECORDER_APP_ID, GITHUB_TEST_RECORDER_APP_KEY and GITHUB_TEST_RECORDER_INSTALLATION_ID from Bitwarden's github-test-recorder-* items"

  Scenario: The janitor's age is more than nothing
    Given the program under test is "integrations"
    When I run "integrations test-org sweep --older-than-hours 0"
    Then the exit code is 2
    And stderr mentions "--older-than-hours is more than 0"
