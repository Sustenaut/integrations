Feature: The wrapper every client call goes through, and the first clients

  Scenario: A record says who called what and how it ended
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "POST /x" with 201 and:
      """
      {"ok": true}
      """
    And the call carries the header "Authorization" set to "secret"
    And the call's body is "12345"
    When the caller calls "github.edit-pr" "open-pr" with "POST https://api.example/x"
    Then the result's "status" is "201"
    And the result's "body" is "{"ok": true}"
    And each call record meets its schema
    And the calls recorded are:
      | repo   | app | plugin         | operation | request_id | status | outcome | bytes_out | bytes_in | retries | mode     | route  |
      | a-repo | web | github.edit-pr | open-pr   | id-1       | 201    | ok      | 5         | 12       | 0       | required | routed |

  Scenario: A record holds no body, URL or token
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "POST /x" with 201 and:
      """
      {"secret-answer": 1}
      """
    And the call carries the header "Authorization" set to "secret-token"
    And the call's body is "secret-body"
    When the caller calls "github.edit-pr" "open-pr" with "POST https://api.example/x"
    Then the result's "status" is "201"
    And no call record mentions "secret"
    And no call record mentions "api.example"

  Scenario: An app may give the request id, so a slow save can be traced
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with 200 and:
      """
      {}
      """
    And the call gives the request id "from-the-app"
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then the calls recorded are:
      | request_id   |
      | from-the-app |

  Scenario: An operation not in the registry is refused
    Given a caller for "a-repo" in the app "web"
    When the caller calls "github.edit-pr" "nope" with "GET https://api.example/x"
    Then it is refused, saying "github.edit-pr nope is not a registered operation"
    And no call was recorded

  Scenario: A refusal comes back to the client and is recorded
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with 404 and:
      """
      {}
      """
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then the result's "status" is "404"
    And the calls recorded are:
      | outcome |
      | refused |

  Scenario Outline: A failure raises and is recorded: <status>
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with <status> and:
      """
      {}
      """
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then it is refused, saying "github.edit-pr open-pr: HTTP <status>"
    And the calls recorded are:
      | outcome     | status   |
      | unavailable | <status> |

    Examples:
      | status |
      | 500    |
      | 429    |

  Scenario: No answer raises and is recorded
    Given a caller for "a-repo" in the app "web"
    And "api.example" does not answer
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then it is refused, saying "github.edit-pr open-pr: no answer"
    And the calls recorded are:
      | outcome     | status |
      | unavailable | null   |

  Scenario: A call a person waits on is not retried
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with 503
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then it is refused, saying "github.edit-pr open-pr: HTTP 503"
    And the "api.example" service was asked "GET /x" 1 times
    And the caller's sleeps were "[]"

  Scenario: A scheduled call is retried with backoff, past no answer, and the retries recorded
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with 503
    And try 2 of the call has no answer
    And the "api.example" service answers "GET /x" with 200
    When the caller calls "github.edit-pr" "check" with "GET https://api.example/x"
    Then the result's "ok" is "true"
    And the "api.example" service was asked "GET /x" 2 times
    And the caller's sleeps were "[1, 2]"
    And the calls recorded are:
      | retries | outcome |
      | 2       | ok      |

  Scenario: A call not safe to repeat is not retried
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "POST /x" with 503
    When the caller calls "github.edit-pr" "check" with "POST https://api.example/x"
    Then it is refused, saying "HTTP 503"
    And the "api.example" service was asked "POST /x" 1 times

  Scenario: A call not safe to repeat is retried when it says it is
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "POST /x" with 503
    And the call says it is safe to repeat
    When the caller calls "github.edit-pr" "check" with "POST https://api.example/x"
    Then it is refused, saying "HTTP 503"
    And the "api.example" service was asked "POST /x" 3 times

  Scenario: A body that can't be read is left to the client
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with 200 and:
      """
      <html>
      """
    And the call takes any JSON body as a refusal
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then the result's "status" is "200"
    And the calls recorded are:
      | outcome |
      | ok      |

  Scenario: A note is a record of time that wasn't a call
    Given a caller for "a-repo" in the app "web"
    And a fake "api.example" service
    When the caller notes "service-total" of "github.edit-pr" taking 9 ms with the status 200
    Then nothing was sent
    And the calls recorded are:
      | operation     | latency_ms | route | bytes_in | status | outcome | request_id |
      | service-total | 9          | none  | 0        | 200    | ok      | r          |

  Scenario: A note of an operation not in the registry is refused
    Given a caller for "a-repo" in the app "web"
    When the caller notes "nope" of "github.edit-pr" taking 1 ms with the status 200
    Then it is refused, saying "not a registered operation"
    And no call was recorded

  Scenario: A refusal is not retried
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with 403
    When the caller calls "github.edit-pr" "check" with "GET https://api.example/x"
    Then the result's "status" is "403"
    And the "api.example" service was asked "GET /x" 1 times

  Scenario: A sink that fails loses the record, not the call
    Given a caller for "a-repo" in the app "web"
    And the caller keeps its records in a sink that fails, saying "disk full"
    And the "api.example" service answers "GET /x" with 200
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then the result's "ok" is "true"
    And stderr mentions "integrations: could not keep a call record: disk full"

  Scenario: The default sink keeps nothing, and the call still works
    Given a caller for "a-repo" in the app "web"
    And the caller keeps its records the default way
    And the "api.example" service answers "GET /x" with 200
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then the result's "ok" is "true"

  Scenario: The file sink appends a line per call
    Given a scratch directory
    And a caller for "a-repo" in the app "web"
    And the caller keeps its records in the file "calls.jsonl"
    And the "api.example" service answers "GET /x" with 200
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    And the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then the file "calls.jsonl" holds 2 call records, each meeting its schema

  Scenario: A caller for a project records that project, and the caller stays its own
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with 200
    When the caller for "o/theirs" calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    And the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then the calls recorded are:
      | repo     |
      | o/theirs |
      | a-repo   |

  Scenario: The wait is random, up to 2 to the n
    Given a caller for "a-repo" in the app "web"
    And the caller's random source gives 0.25
    And the "api.example" service answers "GET /x" with 503
    And the "api.example" service answers "GET /x" with 503
    And the "api.example" service answers "GET /x" with 200
    When the caller calls "github.edit-pr" "check" with "GET https://api.example/x"
    Then the result's "ok" is "true"
    And the caller's sleeps were "[0.25, 0.5]"

  Scenario Outline: A Retry-After date is waited for up to the timeout, and one in the past not at all
    Given a caller for "a-repo" in the app "web"
    And the "api.example" service answers "GET /x" with 503 and the header "Retry-After" set to "<date>"
    And the "api.example" service answers "GET /x" with 200
    When the caller calls "github.edit-pr" "check" with "GET https://api.example/x"
    Then the result's "ok" is "true"
    And the caller's sleeps were "[<waited>]"

    Examples:
      | date                          | waited |
      | Fri, 01 Jan 2100 00:00:00 GMT | 30     |
      | Mon, 01 Jan 2001 00:00:00 GMT | 0.0    |

  Scenario: A call never runs past its timeout times its tries
    Given a caller for "a-repo" in the app "web"
    And a provider that never answers, each try taking all the time it is given
    When the caller calls "github.repo-ops" "call" with "GET https://api.example/x"
    Then it is refused, saying "github.repo-ops call: no answer: timed out"
    And the tries were given "[60, 60, 60, 53]" seconds
    And "240" seconds passed

  Scenario: No try starts that the deadline leaves no time for
    Given a caller for "a-repo" in the app "web"
    And a provider that answers 503 after 29.5 seconds, asking for a retry after 30
    When the caller calls "github.edit-pr" "check" with "GET https://api.example/x"
    Then it is refused, saying "github.edit-pr check: HTTP 503"
    And the tries were given "[30, 30]" seconds

  Scenario: The network transport says who it is, as Cloudflare refuses urllib's name
    Given the network answers 200 with the ETag ""tag"" and the body "body"
    When the network transport sends "GET https://api.example/x" with a timeout of 1 seconds
    Then the network was told the caller is integrations at its version

  Scenario: The network transport sends only over https
    When the network transport sends "GET http://api.example/x" with a timeout of 1 seconds
    Then the result's "error" is "not https"
    And the result's "status" is "null"

  Scenario: The network transport reads an answer
    Given the network answers 200 with the ETag ""tag"" and the body "body"
    And the call's body is "out"
    When the network transport sends "POST https://api.example/x" with a timeout of 7 seconds
    Then the network was sent "POST" with the body "out" and a timeout of 7 seconds
    And the result is:
      """
      {"status": 200, "body": "body", "error": null, "etag": "\"tag\"", "retry_after": null}
      """

  Scenario: The network transport reads a refusal
    Given the network refuses with 403, saying "why"
    When the network transport sends "POST https://api.example/x" with a timeout of 7 seconds
    Then the result is:
      """
      {"status": 403, "body": "why", "error": null, "etag": null, "retry_after": null}
      """

  Scenario: The network transport reads silence
    Given the network is down, saying "down"
    When the network transport sends "POST https://api.example/x" with a timeout of 7 seconds
    Then the result is:
      """
      {"status": null, "body": "", "error": "no answer: down", "etag": null, "retry_after": null}
      """

  Scenario: A recording keeps no token, and can be replayed
    Given a scratch directory
    And a recording of answers where "GET https://api.example/x" is 200 with:
      """
      {"a": 1}
      """
    When the recording sends "GET https://api.example/x" with the header "Authorization" set to "secret"
    And the recording sends "GET https://nothing.example/"
    And the recording is saved as "recorded.json"
    And a replay of "recorded.json" sends "GET https://api.example/x"
    Then the file "recorded.json" does not include "secret"
    And the result's "body" is "{"a": 1}"

  Scenario: A replayed recording has no answer for what was not recorded
    Given a scratch directory
    And a recording of answers where "GET https://api.example/x" is 200 with:
      """
      {"a": 1}
      """
    When the recording sends "GET https://nothing.example/"
    And the recording is saved as "recorded.json"
    And a replay of "recorded.json" sends "GET https://nothing.example/"
    Then the result's "error" is "not recorded"

  Scenario: An empty body reads as nothing
    When I read an empty answer with the status 204 as JSON
    Then the result is empty

  Scenario: Modes come from the repository's integrations.json, required winning
    Given a scratch directory
    And a file "integrations.json" holding:
      """
      {"uses": [{"plugin": "github.edit-pr", "paths": ["a"], "mode": "advisory"},
                {"plugin": "slack.post", "paths": ["a"]},
                {"plugin": "linear.sync", "paths": ["a"], "mode": "advisory"},
                {"plugin": "linear.sync", "paths": ["b"]}],
       "terraform": [{"dir": "infra", "plugins": ["terraform.hetzner"], "runs": [], "mode": "advisory"}]}
      """
    When I read the repository's modes
    Then the result is:
      """
      {"github.edit-pr": "advisory", "slack.post": "required", "linear.sync": "required", "terraform.hetzner": "advisory"}
      """

  Scenario: A plugin's mode reaches the record
    Given a scratch directory
    And a file "integrations.json" holding:
      """
      {"uses": [{"plugin": "github.edit-pr", "paths": ["a"], "mode": "advisory"}]}
      """
    And a caller for "a-repo" in the app "web"
    And the caller takes its modes from the repository
    And the "api.example" service answers "GET /x" with 200
    When the caller calls "github.edit-pr" "open-pr" with "GET https://api.example/x"
    Then the calls recorded are:
      | mode     |
      | advisory |

  Scenario: Everything up is healthy
    Given the plugins' modes:
      | plugin     | mode     |
      | a.required | required |
      | b.advisory | advisory |
    And the checks:
      | plugin     | ok   | detail |
      | a.required | true |        |
      | b.advisory | true |        |
    When I judge the health
    Then the result is:
      """
      {"healthy": true, "unhealthy": [], "alerts": []}
      """

  Scenario: Advisory down only alerts
    Given the plugins' modes:
      | plugin     | mode     |
      | a.required | required |
      | b.advisory | advisory |
    And the checks:
      | plugin     | ok    | detail |
      | a.required | true  |        |
      | b.advisory | false | down   |
    When I judge the health
    Then the result is:
      """
      {"healthy": true, "unhealthy": [], "alerts": ["b.advisory"]}
      """

  Scenario: Required down, or a plugin not listed down, is unhealthy
    Given the plugins' modes:
      | plugin     | mode     |
      | a.required | required |
      | b.advisory | advisory |
    And the checks:
      | plugin     | ok    | detail |
      | a.required | false |        |
      | not.listed | false |        |
    When I judge the health
    Then the result is:
      """
      {"healthy": false, "unhealthy": ["a.required", "not.listed"], "alerts": []}
      """

  Scenario: The version is the same in both places
    When I read the package's version and pyproject.toml's
    Then the result has 1 version

  Scenario Outline: A plugin with a client has a settings schema
    When I check the settings schema of "<plugin>"
    Then it is not refused

    Examples:
      | plugin           |
      | github.edit-pr   |
      | github.app-token |

  Scenario: A refusal from GitHub is refused, and names no query
    Given a caller for "a-repo" in the app "web"
    And GitHub edits of "o/r" with the token "a-token"
    And the "api.github.com" service answers "GET /repos/o/r/git/ref/heads/main" with 200 and:
      """
      {"object": {"sha": "base-sha"}}
      """
    And the "api.github.com" service answers "GET /repos/o/r/contents/a.md?ref=base-sha" with 200 and:
      """
      {"content": "b2xkIHRleHQ=", "sha": "blob-a"}
      """
    And the "api.github.com" service answers "GET /repos/o/r/git/matching-refs/heads/edit/card-" with 200 and:
      """
      []
      """
    And the "api.github.com" service answers "POST /repos/o/r/git/refs" with 201
    And the "api.github.com" service answers "PUT /repos/o/r/contents/a.md" with 200
    And the "api.github.com" service answers "POST /repos/o/r/pulls" with 422 and:
      """
      {}
      """
    And the edit:
      | path | old      | new |
      | a.md | old text | new |
    When I propose the edit on the stem "edit/card-" as "T" with "B"
    Then it is refused, saying "github.edit-pr open-pr: POST /pulls: HTTP 422"

  Scenario: Check passes when the repository can be read
    Given a caller for "a-repo" in the app "web"
    And GitHub edits of "o/r" with the token "a-token"
    And the "api.github.com" service answers "GET /repos/o/r" with 200 and:
      """
      {}
      """
    When I check GitHub edits
    Then the result is:
      """
      {"plugin": "github.edit-pr", "ok": true, "detail": ""}
      """
    And the calls recorded are:
      | operation |
      | check     |

  Scenario: Check fails when the repository can't be read
    Given a caller for "a-repo" in the app "web"
    And GitHub edits of "o/r" with the token "a-token"
    And the "api.github.com" service answers "GET /repos/o/r" with 404 and:
      """
      {}
      """
    When I check GitHub edits
    Then the result's "ok" is "false"
    And the result's "detail" is "GET : HTTP 404"

  Scenario: Check fails when GitHub doesn't answer
    Given a caller for "a-repo" in the app "web"
    And GitHub edits of "o/r" with the token "a-token"
    And "api.github.com" does not answer
    When I check GitHub edits
    Then the result's "ok" is "false"
    And the result's "detail" is "no answer"

  Scenario Outline: The branch number ignores other names
    When I take the next branch on the stem "s-" after '<taken>'
    Then the result is "<branch>"

    Examples:
      | taken                                 | branch |
      | []                                    | s-1    |
      | ["s-2", "s-x", "s-2-b", "other-9"]    | s-3    |

  Scenario: The fake proposes as the client does
    Given a fake of GitHub edits of "o/r" whose base holds "a.md" as "old"
    When the fake proposes changing "a.md" from "old" to "new" on the stem "s-"
    Then the result is "https://github.com/o/r/pull/1"

  Scenario: The fake numbers its pull requests and their branches
    Given a fake of GitHub edits of "o/r" whose base holds "a.md" as "old"
    When the fake proposes changing "a.md" from "old" to "new" on the stem "s-"
    And the fake proposes adding "b.md" holding "new" on the stem "s-"
    Then the result is "https://github.com/o/r/pull/2"

  Scenario: The fake keeps its proposals' branches
    Given a fake of GitHub edits of "o/r" whose base holds "a.md" as "old"
    When the fake proposes changing "a.md" from "old" to "new" on the stem "s-"
    And the fake proposes adding "b.md" holding "new" on the stem "s-"
    And I ask the fake for its proposals' branches
    Then the result is:
      """
      ["s-1", "s-2"]
      """

  Scenario: The fake finds a conflict as the client does
    Given a fake of GitHub edits of "o/r" whose base holds "a.md" as "old"
    When the fake proposes changing "a.md" from "moved" to "new" on the stem "s-"
    Then it is refused, saying "a.md has changed since this edit began"

  Scenario Outline: The fake's check is as it is set
    Given a fake of GitHub edits that is <set>
    When I check the fake of GitHub edits
    Then the result's "ok" is "<ok>"

    Examples:
      | set     | ok    |
      | working | true  |
      | broken  | false |

  Rule: An edit becomes a pull request, unless a file moved

    Background:
      Given a caller for "a-repo" in the app "web"
      And GitHub edits of "o/r" with the token "a-token"
      And the "api.github.com" service answers "GET /repos/o/r/git/ref/heads/main" with 200 and:
        """
        {"object": {"sha": "base-sha"}}
        """
      And the "api.github.com" service answers "GET /repos/o/r/contents/a.md?ref=base-sha" with 200 and:
        """
        {"content": "b2xkIHRleHQ=", "sha": "blob-a"}
        """
      And the "api.github.com" service answers "GET /repos/o/r/contents/new.md?ref=base-sha" with 404
      And the "api.github.com" service answers "GET /repos/o/r/git/matching-refs/heads/edit/card-" with 200 and:
        """
        [{"ref": "refs/heads/edit/card-1"}, {"ref": "refs/heads/edit/card-7"}]
        """
      And the "api.github.com" service answers "POST /repos/o/r/git/refs" with 201
      And the "api.github.com" service answers "PUT /repos/o/r/contents/a.md" with 200
      And the "api.github.com" service answers "PUT /repos/o/r/contents/new.md" with 201
      And the "api.github.com" service answers "POST /repos/o/r/pulls" with 201 and:
        """
        {"html_url": "https://github.com/o/r/pull/9"}
        """

    Scenario: An edit becomes a pull request on the next free branch
      Given the edit:
        | path   | old      | new      |
        | a.md   | old text | new text |
        | new.md | nothing  | n        |
      And the call gives the request id "from-the-app"
      When I propose the edit on the stem "edit/card-" as "T" with "B"
      Then the result is "https://github.com/o/r/pull/9"
      And the "api.github.com" service was sent:
        """
        {"ref": "refs/heads/edit/card-8", "sha": "base-sha"}
        """
      And the "api.github.com" service was sent:
        """
        {"message": "T", "branch": "edit/card-8", "content": "bmV3IHRleHQ=", "sha": "blob-a"}
        """
      And the "api.github.com" service was sent:
        """
        {"message": "T", "branch": "edit/card-8", "content": "bg=="}
        """
      And the "api.github.com" service was sent:
        """
        {"title": "T", "head": "edit/card-8", "base": "main", "body": "B"}
        """
      And every request to the "api.github.com" service carried the header "Authorization" set to "Bearer a-token"
      And the calls recorded are:
        | plugin         | operation | request_id   |
        | github.edit-pr | open-pr   | from-the-app |
        | github.edit-pr | open-pr   | from-the-app |
        | github.edit-pr | open-pr   | from-the-app |
        | github.edit-pr | open-pr   | from-the-app |
        | github.edit-pr | open-pr   | from-the-app |
        | github.edit-pr | open-pr   | from-the-app |
        | github.edit-pr | open-pr   | from-the-app |
        | github.edit-pr | open-pr   | from-the-app |

    Scenario: An edit of a file that moved is a conflict, and changes nothing
      Given the edit:
        | path | old        | new |
        | a.md | what I saw | new |
      When I propose the edit on the stem "edit/card-" as "T" with "B"
      Then it is refused, saying "a.md has changed on main since this edit began"
      And the "api.github.com" service was asked nothing but "GET"

    Scenario: A new file that now exists is a conflict
      Given the edit:
        | path | old     | new |
        | a.md | nothing | new |
      When I propose the edit on the stem "edit/card-" as "T" with "B"
      Then it is refused, saying "a.md has changed on main since this edit began"
      And the "api.github.com" service was asked nothing but "GET"

  Rule: An app's installation token is minted once, and reused until well before it expires

    Background:
      Given a caller for "a-repo" in the app "web"
      And a GitHub App "5" with a private key, installed as "9"
      And the clock reads 1000

    Scenario: A token is minted once and reused
      Given the "api.github.com" service answers "POST /app/installations/9/access_tokens" with 201 and:
        """
        {"token": "first"}
        """
      And the "api.github.com" service answers "POST /app/installations/9/access_tokens" with 201 and:
        """
        {"token": "second"}
        """
      When I ask for the app's token
      And I ask for the app's token
      Then the result is "first"
      And the "api.github.com" service was asked "POST /app/installations/9/access_tokens" 1 times
      And the calls recorded are:
        | plugin           | operation   | route  |
        | github.app-token | mint-in-app | routed |

    Scenario: The token is minted with the app's signed claims
      Given the "api.github.com" service answers "POST /app/installations/9/access_tokens" with 201 and:
        """
        {"token": "first"}
        """
      When I ask for the app's token
      Then the token sent to the "api.github.com" service has the claim "iss" set to "5"
      And the token sent to the "api.github.com" service has the claim "exp" set to "1540"

    Scenario: A new token is minted well before the first expires
      Given the "api.github.com" service answers "POST /app/installations/9/access_tokens" with 201 and:
        """
        {"token": "first"}
        """
      And the "api.github.com" service answers "POST /app/installations/9/access_tokens" with 201 and:
        """
        {"token": "second"}
        """
      When I ask for the app's token
      And 46 minutes pass
      And I ask for the app's token
      Then the result is "second"
      And the "api.github.com" service was asked "POST /app/installations/9/access_tokens" 2 times

    Scenario: A refused token is refused
      Given the "api.github.com" service answers "POST /app/installations/9/access_tokens" with 401 and:
        """
        {}
        """
      When I ask for the app's token
      Then it is refused, saying "github.app-token mint-in-app: HTTP 401"

    Scenario: Check says when the token is refused
      Given the "api.github.com" service answers "POST /app/installations/9/access_tokens" with 401 and:
        """
        {}
        """
      When I check the app's token
      Then the result's "ok" is "false"
      And the result's "detail" is "HTTP 401"

    Scenario: Check says when a token is minted
      Given the "api.github.com" service answers "POST /app/installations/9/access_tokens" with 201 and:
        """
        {"token": "t"}
        """
      When I check the app's token
      Then the result's "ok" is "true"

  Rule: The fake app token signs nothing

    Scenario: The fake hands out its token
      When I ask the fake app for its token
      Then the result is "fake-token"

    Scenario Outline: The fake's check is as it is set
      Given a fake app token that is <set>
      When I check the fake app token
      Then the result's "ok" is "<ok>"

      Examples:
        | set     | ok    |
        | working | true  |
        | broken  | false |
