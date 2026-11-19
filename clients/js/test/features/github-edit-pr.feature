@github-edit-pr
Feature: The generated github.edit-pr client, against a fake integrations service
  The client sends to pubtest's fake "service.example" service, at its address. Its
  request ids come from crypto.randomUUID, which a Given may fix, as it may the clock.

  Background:
    Given a fake "service.example" service
    And no timings are waiting
    And a proposal of:
      """
      {"repo": "o/r", "branch": "b", "title": "t", "changes": {"a.txt": "new\n"}}
      """

  Scenario: It posts to the use case's address with the person's token
    Given the "service.example" service answers "POST /v1/github/edit-pr/proposals" with 200 and:
      """
      {"status": "opened", "url": "u", "number": 1, "branch": "b", "commit": "c"}
      """
    When I propose it to "service.example" as "person" with the request id "r1"
    Then the result is:
      """
      {"status": "opened", "url": "u", "number": 1, "branch": "b", "commit": "c"}
      """
    And the "service.example" service was asked "POST /v1/github/edit-pr/proposals" with the header "Authorization" set to "Bearer person"
    And the "service.example" service was asked "POST /v1/github/edit-pr/proposals" with the header "X-Request-Id" set to "r1"
    And the "service.example" service was sent:
      """
      {"repo": "o/r", "branch": "b", "title": "t", "changes": {"a.txt": "new\n"}}
      """

  Scenario: A stale answer is an answer, not an error
    Given the "service.example" service answers "POST /v1/github/edit-pr/proposals" with 200 and:
      """
      {"status": "stale", "path": "a"}
      """
    When I propose it to "service.example" as "t"
    Then the result is:
      """
      {"status": "stale", "path": "a"}
      """

  Scenario Outline: A refusal throws the service's words, or its status when there are no single words, and the status
    Given the "service.example" service answers "POST /v1/github/edit-pr/proposals" with <status> and:
      """
      <answer>
      """
    When I propose it to "service.example" as "t"
    Then the refusal's "message" is "<message>"
    And the refusal's "status" is "<status>"

    Examples:
      | status | answer                       | message              |
      | 403    | {"detail": "not served here"} | not served here      |
      | 422    | {"detail": [{"msg": "x"}]}    | The service said 422 |

  Scenario Outline: It needs a service and a token
    When I propose it with only <given>
    Then it is refused, saying "<says>"

    Examples:
      | given     | says            |
      | a token   | needs a baseUrl |
      | a service | needs a token   |

  Scenario: A snapshot sends its fields in the query
    Given the "service.example" service answers "GET /v1/github/edit-pr/snapshot?repo=o%2Fr" with 200 and:
      """
      {"branch": "main", "commit": "c", "can_push": true, "paths": ["a"], "truncated": false}
      """
    When I take a snapshot of "o/r" from "service.example" as "t"
    Then the result is:
      """
      {"branch": "main", "commit": "c", "can_push": true, "paths": ["a"], "truncated": false}
      """
    And the "service.example" service was asked "GET /v1/github/edit-pr/snapshot?repo=o%2Fr" with the header "Authorization" set to "Bearer t"

  Scenario: Files are read in one call, in the body
    Given the "service.example" service answers "POST /v1/github/edit-pr/files" with 200 and:
      """
      {"files": {"a": "text", "b": null}}
      """
    When I read the files "a,b" of "o/r" at "c" from "service.example" as "t"
    Then the result is:
      """
      {"files": {"a": "text", "b": null}}
      """
    And the "service.example" service was sent:
      """
      {"repo": "o/r", "commit": "c", "paths": ["a", "b"]}
      """

  Scenario: A call's time waits for a batch
    Given the "service.example" service answers "POST /v1/github/edit-pr/proposals" with 200 and:
      """
      {"status": "stale", "path": "a"}
      """
    When I propose it to "service.example" as "t"
    And I propose it to "service.example" as "t" with the request id "mine"
    Then the "service.example" service was asked "POST /v1/github/edit-pr/proposals" 2 times
    And the "service.example" service was asked "POST /v1/github/edit-pr/timings" 0 times

  Scenario: A call without an id gets one, and its time goes to the service in a batch, with no token
    Given the time stands still
    And new ids are "00000000-0000-4000-8000-000000000001"
    And the "service.example" service answers "POST /v1/github/edit-pr/proposals" with 200 and:
      """
      {"status": "stale", "path": "a"}
      """
    When I propose it to "service.example" as "t"
    And I propose it to "service.example" as "t" with the request id "mine"
    And the timings are sent
    Then the "service.example" service was asked "POST /v1/github/edit-pr/proposals" with the header "X-Request-Id" set to "00000000-0000-4000-8000-000000000001"
    And the "service.example" service was asked "POST /v1/github/edit-pr/timings" 1 times
    And the "service.example" service was asked "POST /v1/github/edit-pr/timings" without the header "Authorization"
    And the "service.example" service was sent:
      """
      {"repo": "o/r", "timings": [{"request_id": "00000000-0000-4000-8000-000000000001", "total_ms": 0},
                                  {"request_id": "mine", "total_ms": 0}]}
      """

  Scenario: A failed call's time is kept too
    Given the time stands still
    And the "service.example" service answers "POST /v1/github/edit-pr/proposals" with 403 and:
      """
      {"detail": "no"}
      """
    When I propose it to "service.example" as "t" with the request id "r1"
    And the timings are sent
    Then the "service.example" service was sent:
      """
      {"repo": "o/r", "timings": [{"request_id": "r1", "total_ms": 0}]}
      """

  Scenario: A batch that can't be sent is let go
    Given the service's fetch answers a proposal, and fails for the timings
    When I propose it to the service as "t"
    And the timings are sent
    Then it is not refused

  Scenario: A full batch sends itself
    Given the "service.example" service answers "POST /v1/github/edit-pr/proposals" with 200 and:
      """
      {"status": "stale", "path": "a"}
      """
    When I propose it to "service.example" as "t" 20 times
    Then the "service.example" service was asked "POST /v1/github/edit-pr/timings" 1 times
