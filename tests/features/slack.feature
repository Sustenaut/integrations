Feature: The Slack clients, against a fake Slack
  Messages go with the bot's token; channels are managed with the workspace owner's.

  Background:
    Given the bot's Slack token "bot" and the owner's "owner"

  Scenario: A post uses the bot token and is recorded
    Given the "slack.com" service answers "POST /api/chat.postMessage" with 200 and:
      """
      {"ok": true}
      """
    When the bot posts "hello" to "C1"
    Then it is not refused
    And the "slack.com" service was asked "POST /api/chat.postMessage" with the header "Authorization" set to "Bearer bot"
    And the "slack.com" service was asked "POST /api/chat.postMessage" with the form:
      | field   | value |
      | channel | C1    |
      | text    | hello |
    And the calls recorded are:
      | plugin     | operation | outcome |
      | slack.post | post      | ok      |

  Scenario: Slack's refusal in a 200 is a refusal
    Given the "slack.com" service answers "POST /api/chat.postMessage" with 200 and:
      """
      {"ok": false, "error": "no_channel"}
      """
    When the bot posts "hello" to "C1"
    Then it is refused, saying "chat.postMessage: no_channel"
    And the calls recorded are:
      | plugin     | operation | outcome |
      | slack.post | post      | refused |

  Scenario: An HTTP refusal is a refusal
    Given the "slack.com" service answers "POST /api/chat.postMessage" with 403 and:
      """
      {}
      """
    When the bot posts "hello" to "C1"
    Then it is refused, saying "chat.postMessage: HTTP 403"

  Scenario: A post is not repeated, as it could be seen twice
    Given the "slack.com" service answers "POST /api/chat.postMessage" with 503 and:
      """
      {}
      """
    When the bot posts "hello" to "C1"
    Then it is refused, saying "slack.post post: HTTP 503"
    And the "slack.com" service was asked "POST /api/chat.postMessage" 1 times

  Scenario: Check for posting says the bot's token works
    Given the "slack.com" service answers "POST /api/auth.test" with 200 and:
      """
      {"ok": true, "user_id": "U1"}
      """
    When the post client is checked
    Then the result is:
      """
      {"plugin": "slack.post", "ok": true, "detail": ""}
      """
    And the "slack.com" service was asked "POST /api/auth.test" with the header "Authorization" set to "Bearer bot"

  Scenario: Check for channels says the owner's token works
    Given the "slack.com" service answers "POST /api/auth.test" with 200 and:
      """
      {"ok": true, "user_id": "U1"}
      """
    When the channels client is checked
    Then the result is:
      """
      {"plugin": "slack.channel-heal", "ok": true, "detail": ""}
      """
    And the "slack.com" service was asked "POST /api/auth.test" with the header "Authorization" set to "Bearer owner"

  Scenario Outline: Check says when the token does not work
    Given the "slack.com" service answers "POST /api/auth.test" with 200 and:
      """
      {"ok": false, "error": "invalid_auth"}
      """
    When the <client> client is checked
    Then the result's "ok" is "false"
    And the result's "detail" is "auth.test: invalid_auth"

    Examples:
      | client   |
      | post     |
      | channels |

  Scenario: Channels follow every page
    Given the "slack.com" service answers "POST /api/conversations.list" with 200 and:
      """
      {"ok": true, "channels": [{"id": "C1", "name": "one", "is_archived": true, "creator": "U1", "purpose": {"value": "why"}}],
       "response_metadata": {"next_cursor": "more"}}
      """
    And the "slack.com" service answers "POST /api/conversations.list" with 200 and:
      """
      {"ok": true, "channels": [{"id": "C2", "name": "two"}], "response_metadata": {"next_cursor": ""}}
      """
    When the channels are listed
    Then the result is:
      """
      [{"id": "C1", "name": "one", "archived": true, "creator": "U1", "purpose": "why"},
       {"id": "C2", "name": "two", "archived": false, "creator": "", "purpose": ""}]
      """
    And the "slack.com" service was asked "POST /api/conversations.list" with the form:
      | field            | value                          |
      | types            | public_channel,private_channel |
      | exclude_archived | false                          |
      | limit            | 1000                           |
      | cursor           |                                |
    And the "slack.com" service was asked "POST /api/conversations.list" with the form:
      | field            | value                          |
      | types            | public_channel,private_channel |
      | exclude_archived | false                          |
      | limit            | 1000                           |
      | cursor           | more                           |

  Scenario: A new channel invites the bot, with the owner's token
    Given the "slack.com" service answers "POST /api/conversations.create" with 200 and:
      """
      {"ok": true, "channel": {"id": "C9", "name": "new"}}
      """
    And the "slack.com" service answers "POST /api/auth.test" with 200 and:
      """
      {"ok": true, "user_id": "U-bot"}
      """
    And the "slack.com" service answers "POST /api/conversations.invite" with 200 and:
      """
      {"ok": true}
      """
    When the channel "new" is created
    Then the result is:
      """
      {"id": "C9", "name": "new", "archived": false, "creator": "", "purpose": ""}
      """
    And the "slack.com" service was asked "POST /api/auth.test" with the header "Authorization" set to "Bearer bot"
    And the "slack.com" service was asked "POST /api/conversations.invite" with the header "Authorization" set to "Bearer owner"
    And the "slack.com" service was asked "POST /api/conversations.invite" with the form:
      | field   | value |
      | channel | C9    |
      | users   | U-bot |

  Scenario Outline: Each change uses the owner's token
    Given the "slack.com" service answers "POST /api/<called>" with 200 and:
      """
      {"ok": true}
      """
    When the channels client calls "<method>" with "<args>"
    Then it is not refused
    And the "slack.com" service was asked "POST /api/<called>" with the header "Authorization" set to "Bearer owner"
    And the "slack.com" service was asked "POST /api/<called>" with the form:
      | field   | value   |
      | channel | C1      |
      | <field> | <value> |
    And the calls recorded are:
      | plugin             | operation |
      | slack.channel-heal | change    |

    Examples:
      | method      | args   | called                   | field   | value |
      | rename      | C1,n   | conversations.rename     | name    | n     |
      | set_purpose | C1,why | conversations.setPurpose | purpose | why   |

  Scenario Outline: Archiving and unarchiving send only the channel, with the owner's token
    Given the "slack.com" service answers "POST /api/<called>" with 200 and:
      """
      {"ok": true}
      """
    When the channels client calls "<method>" with "C1"
    Then it is not refused
    And the "slack.com" service was asked "POST /api/<called>" with the header "Authorization" set to "Bearer owner"
    And the "slack.com" service was asked "POST /api/<called>" with the form:
      | field   | value |
      | channel | C1    |
    And the calls recorded are:
      | plugin             | operation |
      | slack.channel-heal | change    |

    Examples:
      | method    | called                  |
      | archive   | conversations.archive   |
      | unarchive | conversations.unarchive |

  Scenario: The post fake keeps what was posted, and its check passes
    When the post fake posts "hi" to "C1"
    Then the result's "posted" is "[["C1", "hi"]]"
    And the result's "check.ok" is "true"

  Scenario: A post fake set to fail refuses
    When a post fake set to fail posts "hi" to "C1"
    Then it is refused

  Scenario Outline: The fakes' checks say whether they work
    When the <fake> fake that is <set> is checked
    Then the result's "ok" is "<ok>"

    Examples:
      | fake     | set     | ok    |
      | post     | broken  | false |
      | channels | working | true  |
      | channels | broken  | false |

  Scenario: The channels fake keeps each change
    When the channels fake creates "one", renames it "two", sets its purpose "why" and archives it
    Then the result is:
      """
      [{"id": "C1", "name": "two", "archived": true, "creator": "", "purpose": "why"}]
      """

  Scenario: The channels fake unarchives
    Given the channels fake has an archived channel
    When the channels fake unarchives it
    Then the result's "0.archived" is "false"

  Scenario: The channels fake knows whose a token is
    Given the channels fake knows the token "bot" as "U-bot"
    When the channels fake is asked whose the token "bot" is
    Then the result is "U-bot"

  Scenario: The command posts with the token from the environment
    Given the "slack.com" service answers "POST /api/chat.postMessage" with 200 and:
      """
      {"ok": true}
      """
    And the environment sets "SLACK_TOKEN" to "from-env"
    And the environment sets "GITHUB_REPOSITORY" to "o/r"
    When the command line "slack post --channel U1 --text hi" runs
    Then it succeeds
    And the "slack.com" service was asked "POST /api/chat.postMessage" with the header "Authorization" set to "Bearer from-env"

  Scenario: The command says when Slack refuses
    Given the "slack.com" service answers "POST /api/chat.postMessage" with 200 and:
      """
      {"ok": false, "error": "nope"}
      """
    And the environment sets "SLACK_TOKEN" to "t"
    When the command line "slack post --channel U1 --text hi" runs
    Then the exit code is 1
    And stderr mentions "nope"
