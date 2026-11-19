Feature: clients/js/caller.js: what tests/vectors/ doesn't cover
  The caller's fetch goes to pubtest's fake service named by the URL's host, unless a
  Given makes it a stand-in, which says on stdout what it was given; the caller's sleeps
  are said on stdout too ("slept 2"), and don't wait.

  A stand-in fetch that answers says on stdout, as a line of JSON, what it was given.

  Background:
    Given a fake "api.example" service

  Scenario: The policies are classes.json's
    When I compare caller.js's policies with "src/integrations/classes.json"
    Then the result is empty

  Scenario: The operations are the plugins'
    When I compare caller.js's operations of "github.repo-ops" with "src/integrations/plugins/github.repo-ops.json"
    Then the result is empty

  Scenario Outline: The tables can't be changed
    When I ask whether "<table>" from "caller.js" is frozen
    Then the result is "true"

    Examples:
      | table      |
      | POLICIES   |
      | OPERATIONS |

  Scenario: A record has CallRecord's fields, in its order, and no body, URL or token
    Given a caller
    And the time is "2027-01-15T08:00:00Z"
    And its clock reads, in turn, "0.25, 7.75"
    And the "api.example" service answers "POST /x" with 201 and:
      """
      {"secret-answer": 1}
      """
    When it calls "github.edit-pr" "open-pr" with:
      """
      {"method": "POST", "url": "https://api.example/x", "headers": {"Authorization": "secret-token"}, "body": "secret-body"}
      """
    Then the calls recorded have the fields, in order:
      """
      at repo app plugin operation request_id latency_ms status outcome retries bytes_in bytes_out mode route
      """
    And the calls recorded are:
      | at                   | repo   | app | plugin         | operation | request_id | latency_ms | status | outcome |
      | 2027-01-15T08:00:00Z | a-repo | web | github.edit-pr | open-pr   | id-1       | 7          | 201    | ok      |
    And the calls recorded do not include "secret"
    And the calls recorded do not include "api.example"

  Scenario: An operation not in the registry is refused for a call
    Given a caller
    When it calls "github.edit-pr" "nope" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then it is refused, saying "github.edit-pr nope is not a registered operation"
    And no call was recorded
    And nothing was sent

  Scenario: An operation not in the registry is refused for a note
    Given a caller
    When it notes "github.edit-pr" "nope" with:
      """
      {"requestId": "r", "latencyMs": 1, "status": null, "outcome": "ok"}
      """
    Then the refusal's "constructor.name" is "RangeError"
    And no call was recorded

  Scenario: The answer is what the client reads
    Given a caller
    And the "api.example" service answers "GET /x" with 200 and:
      """
      {"permissions": {"push": true}}
      """
    When it calls "github.repo-ops" "check-push" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the result's "constructor.name" is "Answer"
    And the result's "ok" is "true"
    And the result's "status" is "200"

  Scenario: The answer's body reads as JSON
    Given a caller
    And the "api.example" service answers "GET /x" with 200 and:
      """
      {"permissions": {"push": true}}
      """
    When it calls "github.repo-ops" "check-push" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    And I read the answer's JSON
    Then the result is:
      """
      {"permissions": {"push": true}}
      """

  Scenario: An answer with no body reads as null
    When I read the JSON of an answer with status 204
    Then the result is "null"

  Scenario: An answer with no status is not ok
    When I make an answer with no status
    Then the result's "ok" is "false"

  Scenario: The call is sent with its method, headers, body, a User-Agent and a class timeout
    Given a caller
    And its fetch is a stand-in that answers 200 with the header "ETag" set to "\"e1\""
    When it calls "github.edit-pr" "open-pr" with:
      """
      {"method": "PUT", "url": "https://api.example/x", "headers": {"Authorization": "t"}, "body": {"bytes": [1, 2]}}
      """
    Then the result's "etag" is "\"e1\""
    And it prints the JSON:
      """
      {"url": "https://api.example/x", "method": "PUT", "headers": {"User-Agent": "integrations-caller.js", "Authorization": "t"},
       "body": [1, 2], "signal": "live"}
      """

  Scenario: The call reaches the provider as it was made
    Given a caller
    And the "api.example" service answers "PUT /x" with 200
    When it calls "github.edit-pr" "open-pr" with:
      """
      {"method": "PUT", "url": "https://api.example/x", "headers": {"Authorization": "t"}, "body": "{\"a\": 1}"}
      """
    Then the "api.example" service was asked "PUT /x" 1 times
    And the "api.example" service was asked "PUT /x" with the header "User-Agent" set to "integrations-caller.js"
    And the "api.example" service was asked "PUT /x" with the header "Authorization" set to "t"
    And the "api.example" service was sent:
      """
      {"a": 1}
      """

  Scenario: A provider that doesn't answer within its class's timeout is no answer
    Given a caller
    And its fetch is a stand-in that times out
    When it calls "github.edit-pr" "open-pr" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the refusal's "message" is "github.edit-pr open-pr: no answer: timed out after 10 s"
    And the refusal's "constructor.name" is "Unavailable"
    And the refusal's "name" is "Unavailable"
    And the refusal's "plugin" is "github.edit-pr"
    And the refusal's "operation" is "open-pr"
    And the calls recorded are:
      | status | outcome     |
      | null   | unavailable |

  Scenario: A provider that isn't there is no answer too
    Given a caller
    When it calls "github.edit-pr" "open-pr" with:
      """
      {"method": "GET", "url": "https://elsewhere.example/x"}
      """
    Then the refusal's "message" is "github.edit-pr open-pr: no answer: fetch failed"

  Scenario Outline: Unavailable is an IntegrationError, and Refused one that isn't Unavailable
    When I ask whether "<error>" from "caller.js" extends "<kind>"
    Then the result is "<is>"

    Examples:
      | error       | kind             | is    |
      | Unavailable | IntegrationError | true  |
      | Refused     | IntegrationError | true  |
      | Refused     | Unavailable      | false |

  Scenario: Only https goes out
    Given a caller
    And its fetch is a stand-in that answers 200
    When it calls "github.edit-pr" "open-pr" with:
      """
      {"method": "GET", "url": "http://api.example/x"}
      """
    Then it is refused, saying "not https"
    And stdout is empty

  Scenario: The backoff sleeps by default, in seconds
    Given a caller
    And it sleeps for real
    And its random source gives 1
    And the "api.example" service answers "GET /x" with 503
    And the "api.example" service answers "GET /x" with 200
    When it calls "github.edit-pr" "check" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the result's "ok" is "true"
    And it took at least 990 ms

  Scenario: A note is a record of time that wasn't a call
    Given a caller
    When it notes "github.edit-pr" "service-total" with:
      """
      {"requestId": "r", "latencyMs": 9, "status": 200, "outcome": "ok"}
      """
    Then the calls recorded are:
      | operation     | latency_ms | route | bytes_in | request_id |
      | service-total | 9          | none  | 0        | r          |
    And nothing was sent

  Scenario: forRepo's caller records the other project, as the same app in the same modes
    Given a caller
    And it calls "github.edit-pr" in "advisory" mode
    And the "api.example" service answers "GET /x" with 200
    When the caller for "b-repo" calls "github.edit-pr" "open-pr" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the calls recorded are:
      | repo   | app | mode     |
      | b-repo | web | advisory |

  Scenario: forRepo is a new Caller, and the first is unchanged
    Given a caller
    When I make its caller for "b-repo"
    Then the result's "copy.constructor.name" is "Caller"
    And the result's "copy.repo" is "b-repo"
    And the result's "first.repo" is "a-repo"

  Scenario Outline: A sink that fails loses the record, not the call
    Given a caller
    And its sink <fails>, saying "<why>"
    And the "api.example" service answers "GET /x" with 200
    When it calls "github.edit-pr" "open-pr" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the result's "ok" is "true"
    And stderr has a line matching "integrations: could not keep a call record: <why>"

    Examples:
      | fails   | why         |
      | throws  | disk full   |
      | rejects | bucket gone |

  Scenario: The default sink keeps nothing
    When a record is written to the default sink
    Then it is not refused

  Scenario: The default fetch is the global one
    Given the global fetch answers 200
    When a caller with only a repository calls "github.edit-pr" "open-pr" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the result's "ok" is "true"

  Scenario: Refused is for clients, as in Python
    When I make a "Refused" from "caller.js" of:
      """
      ["github.edit-pr", "open-pr", "no"]
      """
    Then the result's "constructor.name" is "Refused"
    And the result's "message" is "github.edit-pr open-pr: no"

  Scenario: Modes come from integrations.json, required winning
    When I read the modes of:
      """
      {"uses": [{"plugin": "github.edit-pr", "paths": ["a"], "mode": "advisory"}, {"plugin": "slack.post", "paths": ["a"]},
                {"plugin": "linear.sync", "paths": ["a"], "mode": "advisory"}, {"plugin": "linear.sync", "paths": ["b"]}],
       "terraform": [{"dir": "infra", "plugins": ["terraform.hetzner"], "runs": [], "mode": "advisory"}]}
      """
    Then the result is:
      """
      {"github.edit-pr": "advisory", "slack.post": "required", "linear.sync": "required", "terraform.hetzner": "advisory"}
      """

  Scenario: An integrations.json that declares nothing has no modes
    When I read the modes of:
      """
      {}
      """
    Then the result is empty

  Scenario: A replay answers in turn, keeps its last answer, and a null-body status has none
    Given a replay of:
      """
      {"GET https://api.example/x": [{"status": 503, "json": {}}, {"status": 204}]}
      """
    When I fetch "https://api.example/x" from it 3 times
    Then the result is:
      """
      [503, 204, 204]
      """

  Scenario: A replay has no answer for what it didn't record
    Given a replay of:
      """
      {"GET https://api.example/x": {"status": 200, "json": {}}}
      """
    When I fetch "https://other.example/" from it 1 times
    Then it is refused, saying "no recorded call"

  Scenario: Json is written as Python's json.dumps writes it, anything outside ASCII escaped
    When I write as Python's json.dumps does:
      """
      {"a": [1, true, null], "\u00e9": "x\u2028\n"}
      """
    Then the result is:
      """
      {"a": [1, true, null], "\u00e9": "x\u2028\n"}
      """

  Scenario Outline: Json writes an empty list and map as Python's does
    When I write as Python's json.dumps does:
      """
      <value>
      """
    Then the result is "<value>"

    Examples:
      | value |
      | []    |
      | {}    |

  Scenario Outline: A Retry-After date is waited for up to the timeout, and one in the past not at all
    Given a caller
    And the "api.example" service answers "GET /x" with 503 and a Retry-After an hour <when>
    And the "api.example" service answers "GET /x" with 200
    When it calls "github.edit-pr" "check" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the result's "ok" is "true"
    And it prints:
      """
      slept <waited>
      """

    Examples:
      | when  | waited |
      | ahead | 30     |
      | ago   | 0      |

  Scenario: A call never runs past its timeout times its tries
    Given a caller
    And its random source gives 1
    And its clock moves only as it sleeps and as each try takes its whole timeout
    And its fetch is a stand-in that times out
    When it calls "github.repo-ops" "call" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the refusal's "constructor.name" is "Unavailable"
    And it prints:
      """
      timeout 60000
      slept 1
      timeout 60000
      slept 2
      timeout 60000
      slept 4
      timeout 53000
      """
    And the calls recorded are:
      | latency_ms | retries |
      | 240000     | 3       |

  Scenario: No try starts that the deadline leaves no time for
    Given a caller
    And its clock moves only as it sleeps and as each try takes 29.5 s
    And its fetch is a stand-in that answers 503 with the header "Retry-After" set to "30"
    When it calls "github.edit-pr" "check" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then the refusal's "constructor.name" is "Unavailable"
    And it prints a line matching "slept 30"
    And the calls recorded are:
      | retries |
      | 1       |

  Scenario: The jitter's random source is the caller's, and forRepo keeps it
    Given a caller
    And its random source gives 0.5
    And the "api.example" service answers "GET /x" with 503
    And the "api.example" service answers "GET /x" with 503
    And the "api.example" service answers "GET /x" with 200
    When the caller for "b-repo" calls "github.edit-pr" "check" with:
      """
      {"method": "GET", "url": "https://api.example/x"}
      """
    Then it prints:
      """
      slept 0.5
      slept 1
      """
