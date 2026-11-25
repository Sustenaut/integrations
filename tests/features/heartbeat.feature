Feature: A sender's heartbeat in Cloud Monitoring
  Every CALL_LOG_METRIC_SECONDS, each instance of a sender writes 1 to the gauge
  custom.googleapis.com/sustenaut/heartbeat on the resource global, with exactly the
  labels project, sender and instance, for shared-infra's absence alerts. Google is a
  stand-in that keeps what it is sent.

  Background:
    Given the clock reads 2027-01-15T08:00:00Z

  Scenario: A beat is one point a project, with exactly the three labels, on the resource global
    Given a heartbeat of the instance "app-7d9f8c6b5d-x2k4p" for the datasets ["calls_release_controlplane"]
    When it beats
    Then the result is "true"
    And the request is:
      """
      {"method": "POST", "url": "https://monitoring.googleapis.com/v3/projects/a-project/timeSeries",
       "authorization": "Bearer a-token",
       "body": {"timeSeries": [{
         "metric": {"type": "custom.googleapis.com/sustenaut/heartbeat",
                    "labels": {"project": "release_controlplane", "sender": "app", "instance": "app-7d9f8c6b5d-x2k4p"}},
         "resource": {"type": "global", "labels": {"project_id": "a-project"}},
         "points": [{"interval": {"endTime": "2027-01-15T08:00:00Z"}, "value": {"int64Value": "1"}}]}]}}
      """
    And the heartbeat has sent 1 and failed 0

  Scenario: A sender of two projects beats for each, in one request
    Given a heartbeat of the instance "ingest-x2k4p" for the datasets ["calls_integrations", "calls_storage", "calls_storage"]
    When it beats
    Then it sent 1 requests
    And the result is "true"

  Scenario Outline: A failed beat is counted and said, never raised
    Given a heartbeat of the instance "app-1" for the datasets ["calls_a"]
    And <answer>
    When it beats
    Then the result is "false"
    And the heartbeat has sent 0 and failed 1

    Examples:
      | answer                    |
      | Monitoring answers 403    |
      | Monitoring doesn't answer |

  Scenario: A heartbeat of no project sends nothing
    Given a heartbeat of the instance "app-1" for the datasets []
    When it beats
    Then the result is "true"
    And it sent 0 requests

  Scenario Outline: The sender is the instance's name without its generated suffix
    When the sender prefix of "<instance>" is read
    Then the result is "<sender>"

    Examples:
      | instance              | sender     |
      | app-7d9f8c6b5d-x2k4p  | app        |
      | plan-00012-abc        | plan       |
      | ingest-x2k4p          | ingest     |
      | ingest-0              | ingest     |
      | e2e-proxy             | e2e-proxy  |

  Scenario: Unset, there is no heartbeat
    Given the environment does not set "CALL_LOG_METRIC_SECONDS"
    When the heartbeat is made from the environment, for the datasets ["calls_a"]
    Then the result is "null"

  Scenario: Set, it beats with the credential's account, for the datasets' projects
    Given the environment sets "CALL_LOG_METRIC_SECONDS" to "60"
    And the environment sets "CALL_LOG_METRIC_CREDENTIAL_FILE" to "/var/run/google/credential.json"
    When the heartbeat is made from the environment, for the datasets ["calls_visit_ee"]
    Then the result's "projects" is "["visit_ee"]"
    And the result's "sender" is "app"
    And the result's "google_project" is "a-project"
    And the result's "every_seconds" is "60.0"

  Scenario: GOOGLE_APPLICATION_CREDENTIALS will do for the credential
    Given the environment sets "CALL_LOG_METRIC_SECONDS" to "60"
    And the environment does not set "CALL_LOG_METRIC_CREDENTIAL_FILE"
    And the environment sets "GOOGLE_APPLICATION_CREDENTIALS" to "/var/run/google/credential.json"
    When the heartbeat is made from the environment, for the datasets ["calls_visit_ee"]
    Then the result's "google_project" is "a-project"

  Scenario: Set without a credential is refused
    Given the environment sets "CALL_LOG_METRIC_SECONDS" to "60"
    And the environment does not set "CALL_LOG_METRIC_CREDENTIAL_FILE"
    And the environment does not set "GOOGLE_APPLICATION_CREDENTIALS"
    When the heartbeat is made from the environment, for the datasets ["calls_a"]
    Then it is refused, saying "CALL_LOG_METRIC_SECONDS is set, so the heartbeat needs a Google credential"

  Scenario: A credential file that isn't one is refused
    Given the environment sets "CALL_LOG_METRIC_SECONDS" to "60"
    And the environment sets "CALL_LOG_METRIC_CREDENTIAL_FILE" to "/nowhere.json"
    When the heartbeat is made from the environment, for the datasets ["calls_a"]
    Then it is refused, saying "CALL_LOG_METRIC_CREDENTIAL_FILE names /nowhere.json, which isn't a Google credential"

  Scenario Outline: An interval that isn't at least 10 seconds is refused
    Given the environment sets "CALL_LOG_METRIC_SECONDS" to "<text>"
    When the heartbeat is made from the environment, for the datasets ["calls_a"]
    Then it is refused, saying "CALL_LOG_METRIC_SECONDS is a number of seconds, at least 10"

    Examples:
      | text |
      | 5    |
      | soon |
      | inf  |

  Scenario: A storage sink starts the heartbeat CALL_LOG_METRIC_SECONDS asks for
    Given the environment sets "CALL_LOG_METRIC_SECONDS" to "60"
    And the environment sets "CALL_LOG_METRIC_CREDENTIAL_FILE" to "/var/run/google/credential.json"
    And the credential file holds a key
    When a storage sink is made
    Then the sink's heartbeat writes for "a_repo" as "app"

  Scenario: A storage sink with CALL_LOG_METRIC_SECONDS unset has no heartbeat
    Given the environment does not set "CALL_LOG_METRIC_SECONDS"
    When a storage sink is made
    Then the sink has no heartbeat
