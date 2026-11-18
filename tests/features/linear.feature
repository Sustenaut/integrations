Feature: The Linear client, against a fake Linear
  A query is safe to repeat, so a scheduled one is retried; a mutation is not. Linear
  explains a refusal in the body, as a 400 or as errors beside a 200.

  Background:
    Given the Linear key "a-key"

  Scenario: A query answers its data and sends the key as Linear wants
    Given the "api.linear.app" service answers "POST /graphql" with 200 and:
      """
      {"data": {"viewer": {"id": "1"}}}
      """
    When the query "{ viewer { id } }" is sent with the variables:
      """
      {"a": 1}
      """
    Then the result is:
      """
      {"viewer": {"id": "1"}}
      """
    And the "api.linear.app" service was asked "POST /graphql" with the header "Authorization" set to "a-key"
    And the "api.linear.app" service was sent:
      """
      {"query": "{ viewer { id } }", "variables": {"a": 1}}
      """
    And the calls recorded are:
      | plugin      | operation |
      | linear.sync | sync      |

  Scenario: The operation says who is waiting
    Given the "api.linear.app" service answers "POST /graphql" with 200 and:
      """
      {"data": {}}
      """
    And the Linear client reads for a person
    When the query "{ a }" is sent with the request id "from-the-app"
    Then it is not refused
    And the calls recorded are:
      | operation | request_id   |
      | read      | from-the-app |

  Scenario Outline: Errors beside a 200 and in a 400 are refusals with Linear's reason
    Given the "api.linear.app" service answers "POST /graphql" with <status> and:
      """
      <body>
      """
    When the query "{ a }" is sent
    Then it is refused, saying "linear.sync sync: <says>"
    And the calls recorded are:
      | outcome |
      | refused |

    Examples:
      | status | body                                                 | says          |
      | 200    | {"errors": [{"message": "no such field"}]}           | no such field |
      | 400    | {"errors": [{"message": "bad"}, {"message": "worse"}]} | bad; worse    |
      | 401    | {}                                                   | HTTP 401      |

  Scenario: A refusal with a body that is not JSON still says its status
    Given the "api.linear.app" service answers "POST /graphql" with 403 and:
      """
      <html>
      """
    When the query "{ a }" is sent
    Then it is refused, saying "linear.sync sync: HTTP 403"

  Scenario: A query is retried on a timer
    Given the "api.linear.app" service answers "POST /graphql" with 503 and:
      """
      {}
      """
    When the query "{ a }" is sent
    Then it is refused, saying "linear.sync sync: HTTP 503"
    And the "api.linear.app" service was asked "POST /graphql" 3 times

  Scenario: A mutation is not retried
    Given the "api.linear.app" service answers "POST /graphql" with 503 and:
      """
      {}
      """
    When the query "  mutation { a }" is sent
    Then it is refused, saying "linear.sync sync: HTTP 503"
    And the "api.linear.app" service was asked "POST /graphql" 1 times

  Scenario: A query a person waits on is not retried
    Given the "api.linear.app" service answers "POST /graphql" with 503 and:
      """
      {}
      """
    And the Linear client reads for a person
    When the query "{ a }" is sent
    Then it is refused, saying "linear.sync read: HTTP 503"
    And the "api.linear.app" service was asked "POST /graphql" 1 times

  Scenario: Check says the key works
    Given the "api.linear.app" service answers "POST /graphql" with 200 and:
      """
      {"data": {"viewer": {"id": "1"}}}
      """
    When the Linear client is checked
    Then the result is:
      """
      {"plugin": "linear.sync", "ok": true, "detail": ""}
      """

  Scenario: Check says the key does not work
    Given the "api.linear.app" service answers "POST /graphql" with 401 and:
      """
      {}
      """
    When the Linear client is checked
    Then the result's "ok" is "false"
    And the result's "detail" is "HTTP 401"

  Scenario: The fake answers in turn and keeps what was asked
    Given a Linear fake that answers:
      """
      [{"a": 1}]
      """
    When the fake is asked "{ a }" with {"x": 1} and then "{ b }"
    Then the result is:
      """
      {"answers": [{"a": 1}, {}], "asked": [["{ a }", {"x": 1}], ["{ b }", {}]]}
      """

  Scenario Outline: The fake's check says whether it works
    Given a Linear fake that is <set>
    When the Linear fake is checked
    Then the result's "ok" is "<ok>"

    Examples:
      | set     | ok    |
      | working | true  |
      | broken  | false |

  Scenario: A fake set to fail refuses
    Given a Linear fake that is broken
    When the fake is asked "{ a }"
    Then it is refused
