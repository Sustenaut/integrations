Feature: The registry and the consumer format, held to their JSON schemas

  Scenario: Every plugin meets the plugin schema and is named by its file
    When I hold every plugin to the plugin schema
    Then the result is empty

  Scenario: Every shared record file (plugins/_name.json) meets its part of the schema
    When I hold every shared record file to its schema
    Then the result is empty

  Scenario: A shared record file is not a plugin
    When I list the plugins
    Then the result includes "github.edit-pr"
    And the result does not include "_github"

  Scenario: Only a record block extends shared rules
    Given the plugin "github.edit-pr" with "extends" set to "_github" at its top
    When I hold it to the plugin schema
    Then it is refused, saying "Additional properties are not allowed ('extends' was unexpected)"

  Scenario: A plugin's record rules are its own over what it extends, over the defaults
    Given a scratch directory
    And a file "plugins/_base.json" holding:
      """
      {"description": "shared", "record": {"keep_headers": ["ETag"], "secret": {"query": ["key"]}, "match": {"body": "text"}}}
      """
    And a file "plugins/x.y.json" holding:
      """
      {"record": {"extends": "_base", "keep_headers": ["Link", "ETag"], "secret": {"json": ["$.token"]}, "customers": "x.test"}}
      """
    When I read the record rules of "x.y" from the plugins in "plugins"
    Then the result is:
      """
      {"keep_headers": ["ETag", "Link"],
       "volatile": {"headers": [], "json": [], "values": {}},
       "secret": {"headers": ["Authorization", "Proxy-Authorization", "Cookie", "Set-Cookie"], "json": ["$.token"], "query": ["key"]},
       "match": {"url": "scheme+host+path+sorted-query", "body": "text", "graphql": null},
       "customers": "x.test"}
      """

  Scenario: A record block extending nothing there is refused
    Given a scratch directory
    And a file "plugins/x.y.json" holding "{"record": {"extends": "_gone"}}"
    When I read the record rules of "x.y" from the plugins in "plugins"
    Then it is refused, saying "no plugin or shared record rules _gone"

  Scenario: Shared record rules that extend themselves are refused
    Given a scratch directory
    And a file "plugins/_a.json" holding "{"record": {"extends": "_b"}}"
    And a file "plugins/_b.json" holding "{"record": {"extends": "_a"}}"
    And a file "plugins/x.y.json" holding "{"record": {"extends": "_a"}}"
    When I read the record rules of "x.y" from the plugins in "plugins"
    Then it is refused, saying "record rules extend themselves: x.y -> _a -> _b -> _a"

  Scenario: Every signature compiles
    When I compile every plugin's signatures
    Then the result is empty

  Scenario: A plugin with no signatures must be manual only
    Given the plugin "linear.sync" with no signatures
    When I hold it to the plugin schema
    Then it is refused, saying "'M' was expected"

  Scenario: Every inventory row has its plugins and every plugin a row
    When I compare the inventory with the registry
    Then the result's "rows" is "33"
    And the result's "not listed" is "[]"
    And the result's "not plugins" is "[]"

  Scenario: The classes are the four
    When I read the classes
    Then the result has 4 classes
    And the result includes "H"
    And the result includes "E"
    And the result includes "S"
    And the result includes "M"

  Scenario Outline: Every class has its targets, and how a caller calls in it
    When I read the class "<class>"
    Then the result includes "response_p95"
    And the result includes "uptime"
    And the result includes "on_failure"
    And the result's "timeout" is "<timeout>"
    And the result's "tries" is "<tries>"

    Examples:
      | class | timeout | tries |
      | H     | 10      | 1     |
      | E     | 60      | 4     |
      | S     | 30      | 3     |
      | M     | 30      | 1     |

  Scenario: The registry names no consumer, as consumers name themselves in their own repositories
    When I look for a consumer's name in the plugins and not-providers.json
    Then the result is empty

  Scenario Outline: The loader and the schema accept the same
    When the schema and the loader read '<data>'
    Then the result is:
      """
      {"schema": "accepted", "loader": "accepted"}
      """

    Examples:
      | data |
      | {} |
      | {"uses": [{"plugin": "linear.sync", "paths": ["tools/**"]}]} |
      | {"uses": [{"plugin": "inwx.registrar", "paths": []}]} |
      | {"uses": [{"plugin": "linear.sync", "paths": [], "mode": "advisory"}]} |
      | {"terraform": [{"dir": "infra", "plugins": ["linear.sync"], "runs": [], "mode": "required"}]} |
      | {"terraform": [{"dir": "infra", "plugins": ["linear.sync"], "runs": ["laptop", "actions"]}]} |
      | {"own_hosts": ["mine.example"], "other_hosts": [{"host": "a.example", "reason": "why"}], "other_secrets": [{"name": "A", "reason": "why"}], "exclude": [{"path": "tests/**", "reason": "why"}]} |

  Scenario Outline: The loader and the schema refuse the same
    When the schema and the loader read '<data>'
    Then the result is:
      """
      {"schema": "refused", "loader": "refused"}
      """

    Examples:
      | data |
      | [] |
      | {"nope": []} |
      | {"uses": {}} |
      | {"uses": [{"plugin": "linear.sync"}]} |
      | {"uses": [{"plugin": "linear.sync", "paths": [""]}]} |
      | {"uses": [{"plugin": "linear.sync", "paths": [], "extra": 1}]} |
      | {"uses": [{"plugin": "linear.sync", "paths": [], "mode": "optional"}]} |
      | {"terraform": [{"dir": "infra", "plugins": ["linear.sync"], "runs": [], "mode": "optional"}]} |
      | {"terraform": {}} |
      | {"terraform": [{"dir": "infra", "plugins": ["linear.sync"]}]} |
      | {"terraform": [{"dir": "infra", "plugins": ["linear.sync"], "runs": ["cloud"]}]} |
      | {"own_hosts": "mine.example"} |
      | {"other_hosts": {}} |
      | {"other_hosts": [{"host": "a.example"}]} |
      | {"other_secrets": [{"name": "A", "reason": ""}]} |
      | {"exclude": [{"path": "tests/**"}]} |

  Scenario Outline: Path patterns
    When I match "<path>" against the path pattern "<pattern>"
    Then the result is "<matches>"

    Examples:
      | pattern  | path         | matches |
      | a.py     | a.py         | true    |
      | a.py     | b/a.py       | false   |
      | tests/   | tests/x/y.py | true    |
      | tests/** | tests/y.py   | true    |
      | tests/** | testsx/y.py  | false   |
      | **/*.py  | a.py         | true    |
      | **/*.py  | b/c/a.py     | true    |
      | *.py     | b/a.py       | false   |
      | a?.py    | ab.py        | true    |
