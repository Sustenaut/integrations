Feature: cloudflare.dns's check, against a fake Cloudflare

  Background:
    Given a fake "api.cloudflare.com" service
    And the Cloudflare token "a-token"

  Scenario: An active token passes and is sent as Cloudflare wants
    Given the "api.cloudflare.com" service answers "GET /client/v4/user/tokens/verify" with 200 and:
      """
      {"result": {"status": "active"}}
      """
    When I check Cloudflare
    Then the result's "ok" is "true"
    And the "api.cloudflare.com" service was asked "GET /client/v4/user/tokens/verify" with the header "Authorization" set to "Bearer a-token"

  Scenario Outline: Anything else fails and says why
    Given the "api.cloudflare.com" service answers "GET /client/v4/user/tokens/verify" with <status> and:
      """
      <body>
      """
    When I check Cloudflare
    Then the result's "ok" is "false"
    And the result's "detail" is "<says>"

    Examples:
      | status | body                                | says                         |
      | 200    | {"result": {"status": "expired"}}   | the token is expired         |
      | 401    | {}                                  | the token is refused: HTTP 401 |

  Scenario: No answer fails and says so
    Given "api.cloudflare.com" does not answer
    When I check Cloudflare
    Then the result's "ok" is "false"
    And the result's "detail" is "no answer"

  Scenario Outline: The fake passes or fails as it is set
    When I check a fake Cloudflare that is <set>
    Then the result's "ok" is "<ok>"

    Examples:
      | set     | ok    |
      | working | true  |
      | broken  | false |
