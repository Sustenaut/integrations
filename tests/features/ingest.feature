Feature: The ingest pod's parts: its BigQuery loads, its Google token, its Avro and its health

  Scenario: Avro is the same bytes for the same records
    Given the call records 1 and 2, the second with no status
    When I encode them, and again from copies
    Then the result is "true"

  Scenario: Avro keeps every field
    Given the call records 1 and 2, the second with no status
    When I encode them and read them back
    Then the result is:
      """
      [{"at": "2026-11-02T10:00:00Z", "repo": "a-repo", "app": "web", "plugin": "github.edit-pr",
        "operation": "open-pr", "request_id": "r1", "latency_ms": 12, "status": 201, "outcome": "ok",
        "retries": 0, "bytes_in": 3, "bytes_out": 4, "mode": "required", "route": "routed"},
       {"at": "2026-11-02T10:00:00Z", "repo": "a-repo", "app": "web", "plugin": "github.edit-pr",
        "operation": "open-pr", "request_id": "r2", "latency_ms": 12, "status": null, "outcome": "ok",
        "retries": 0, "bytes_in": 3, "bytes_out": 4, "mode": "required", "route": "routed"}]
      """

  Scenario Outline: A load says whether another try may work
    Given BigQuery answers a load "<answer>"
    When I load call record 1 into "ds" as "calls_x"
    Then the result's "outcome" is "<outcome>"
    And the result's "ended" is "<ended>"

    Examples:
      | answer      | outcome  | ended |
      | silent      | retry    | false |
      | busy        | retry    | false |
      | job-busy    | retry    | true  |
      | running     | retry    | false |
      | forbidden   | retry    | false |
      | bad-request | rejected | false |
      | invalid     | rejected | true  |
      | ok          | loaded   | false |

  Scenario: A lost answer finds the job already there
    Given BigQuery answers a load "ok"
    And call record 1 was loaded into "ds" as "calls_x"
    When I load call record 1 into "ds" as "calls_x"
    Then the result's "outcome" is "loaded"
    And the result's "rows loaded" is "1"

  Scenario: Ingest answers health
    When I GET "/healthz"
    Then the answer is 200
    And the answer is the JSON:
      """
      {"status": "ok", "unhealthy": []}
      """

  Scenario: Ingest answers that it lives
    When I GET "/livez"
    Then the answer is 200
    And the answer is the JSON:
      """
      {"status": "ok"}
      """

  Scenario Outline: Ingest has no public docs
    When I GET "<path>"
    Then the answer is 404

    Examples:
      | path          |
      | /docs         |
      | /openapi.json |

  Scenario: Ingest takes no records over HTTP
    When I POST to "/records" with:
      """
      {}
      """
    Then the answer is 404

  Scenario Outline: The scanner works while it finishes a pass every three intervals of 120 seconds
    When I check a scanner whose last pass was <seconds> seconds ago
    Then the result's "ok" is "<ok>"

    Examples:
      | seconds | ok    |
      | 359     | true  |
      | 360     | false |

  Scenario: A scanner that stops makes the pod unready
    Given the scanner's last pass was 360 seconds ago
    When I GET "/healthz"
    Then the answer is 503
    And the answer is the JSON:
      """
      {"status": "unhealthy", "unhealthy": ["ingest.scanner"]}
      """

  Scenario: A scanner that stops leaves the pod alive
    Given the scanner's last pass was 360 seconds ago
    When I GET "/livez"
    Then the answer is 200

  Scenario: The record fields are the schemas
    When I compare the call record's fields with its JSON schema's and its Avro schema's
    Then the result is empty

  Scenario: A Google token is traded in once and reused
    Given a service account "w@x" whose key trades at "https://oauth.example/token"
    And the "oauth.example" service answers "POST /token" with 200 and:
      """
      {"access_token": "first"}
      """
    When I ask for its token twice
    Then the result is:
      """
      ["first", "first"]
      """
    And the "oauth.example" service was asked "POST /token" 1 times
    And the "oauth.example" service was sent a form with "grant_type" set to "urn:ietf:params:oauth:grant-type:jwt-bearer"
    And no secret appears in what it produced

  Scenario: A Google token is traded in again before it expires
    Given a service account "w@x" whose key trades at "https://oauth.example/token"
    And the "oauth.example" service answers "POST /token" with 200 and:
      """
      {"access_token": "first"}
      """
    And the "oauth.example" service answers "POST /token" with 200 and:
      """
      {"access_token": "second"}
      """
    And its token was asked for
    And 3400 seconds pass
    When I ask for its token
    Then the result is "second"
    And the "oauth.example" service was asked "POST /token" 2 times

  Scenario: A refused Google token is a refusal
    Given a service account "w" whose key trades at "https://o.example/t"
    And the "o.example" service answers "POST /t" with 401
    When I ask for its token
    Then it is refused, saying "google.bigquery load: token: HTTP 401"

  Scenario: A federated token trades the pod's token, then impersonates, and is reused
    Given the pod's external account credential, its token "pod-token"
    And the "sts.googleapis.com" service answers "POST /v1/token" with 200 and:
      """
      {"access_token": "federated"}
      """
    And the "iamcredentials.googleapis.com" service answers "POST /v1/projects/-/serviceAccounts/reader@a-project.iam.gserviceaccount.com:generateAccessToken" with 200 and:
      """
      {"accessToken": "access-2"}
      """
    When I ask for its token twice
    Then the result is:
      """
      ["access-2", "access-2"]
      """
    And the "sts.googleapis.com" service was asked "POST /v1/token" 1 times
    And the "sts.googleapis.com" service was sent a form with "subject_token" set to "pod-token"
    And the "sts.googleapis.com" service was sent a form with "audience" set to "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/services/providers/k3s"
    And the "iamcredentials.googleapis.com" service was asked "POST /v1/projects/-/serviceAccounts/reader@a-project.iam.gserviceaccount.com:generateAccessToken" 1 times
    And the "iamcredentials.googleapis.com" service was asked "POST /v1/projects/-/serviceAccounts/reader@a-project.iam.gserviceaccount.com:generateAccessToken" with the header "Authorization" set to "Bearer federated"
    And the pod's token was read once, from "/var/run/secrets/google/token"

  Scenario: A federated token is traded in again before it expires
    Given the pod's external account credential, its token "pod-token"
    And the "sts.googleapis.com" service answers "POST /v1/token" with 200 and:
      """
      {"access_token": "federated"}
      """
    And the "iamcredentials.googleapis.com" service answers "POST /v1/projects/-/serviceAccounts/reader@a-project.iam.gserviceaccount.com:generateAccessToken" with 200 and:
      """
      {"accessToken": "access-2"}
      """
    And the "iamcredentials.googleapis.com" service answers "POST /v1/projects/-/serviceAccounts/reader@a-project.iam.gserviceaccount.com:generateAccessToken" with 200 and:
      """
      {"accessToken": "access-4"}
      """
    And its token was asked for
    And 3400 seconds pass
    When I ask for its token
    Then the result is "access-4"
    And the "sts.googleapis.com" service was asked "POST /v1/token" 2 times

  Scenario Outline: A credential's project
    When I read the project of <credential>
    Then the result is "<project>"

    Examples:
      | credential                        | project   |
      | the pod's external account credential | a-project |
      | a key whose project_id is "p"     | p         |

  Scenario: A credential that names no project is refused
    When I read the project of an external account credential with no account
    Then it is refused, saying "the credential names no project"

  Scenario Outline: The token for a credential is its kind's
    When I make the Google token for <credential>
    Then the result is "<kind>"

    Examples:
      | credential                        | kind           |
      | the pod's external account credential | FederatedToken |
      | a key of "w"                      | GoogleToken    |

  Scenario Outline: A federated token refused at either step is a refusal
    Given the pod's external account credential, its token "t"
    And the "sts.googleapis.com" service answers "POST /v1/token" with <sts> and:
      """
      {"access_token": "federated"}
      """
    And the "iamcredentials.googleapis.com" service answers "POST /v1/projects/-/serviceAccounts/reader@a-project.iam.gserviceaccount.com:generateAccessToken" with <iam> and:
      """
      {"accessToken": "access"}
      """
    When I ask for its token
    Then it is refused, saying "google.bigquery load: <step>: HTTP 403"

    Examples:
      | sts | iam | step           |
      | 403 | 200 | token exchange |
      | 200 | 403 | impersonation  |

  Scenario: A credential that is no external account is said
    When I make a federated token of a service account credential
    Then it is refused, saying "not an external_account credential that impersonates a service account"

  Scenario: The ingest's token, its credential and the provider name one audience
    When I read the ingest's Google credential and its Deployment in k8s/
    Then the result is:
      """
      {"audience": "//iam.googleapis.com/projects/948171295184/locations/global/workloadIdentityPools/services/providers/sustenaut-k3s",
       "project": "sustenaut",
       "token": "FederatedToken",
       "service account": "ingest",
       "automounts a token": false,
       "projected token's audience": "//iam.googleapis.com/projects/948171295184/locations/global/workloadIdentityPools/services/providers/sustenaut-k3s",
       "projected token's file": "/var/run/secrets/google/token",
       "credential's token file": "/var/run/secrets/google/token",
       "INGEST_KEY_FILE": "/etc/google/credential.json",
       "credential's file": "/etc/google/credential.json"}
      """
