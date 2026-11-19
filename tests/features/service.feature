Feature: The running service, with GitHub as a fake service

  The service opens pull requests and reads repositories as the person whose
  token comes with the request. It serves "o/r" to "https://site.example".

  Background:
    Given the service serves "o/r" to "https://site.example"

  # Proposals

  Scenario: A proposal is one commit and a pull request as the person
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    And requests carry the header "X-Request-Id" set to "from-the-app"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 200
    And the answer is the JSON:
      """
      {"status": "opened", "url": "https://github.com/o/r/pull/7", "number": 7, "branch": "edit/one", "commit": "commit"}
      """
    And every request to the "api.github.com" service carried the header "Authorization" set to "Bearer person-secret-token"
    And the "api.github.com" service was asked, in turn:
      """
      GET /repos/o/r
      GET /repos/o/r/git/ref/heads/main
      GET /repos/o/r/git/commits/start
      POST /repos/o/r/git/trees
      POST /repos/o/r/git/commits
      POST /repos/o/r/git/refs
      POST /repos/o/r/pulls
      """
    And the "api.github.com" service was sent:
      """
      {"base_tree": "base-tree", "tree": [{"path": "a.txt", "mode": "100644", "type": "blob", "content": "new"}]}
      """
    And the "api.github.com" service was sent:
      """
      {"message": "T\n\nB", "tree": "next-tree", "parents": ["start"]}
      """
    And the "api.github.com" service was sent:
      """
      {"ref": "refs/heads/edit/one", "sha": "commit"}
      """
    And the "api.github.com" service was sent:
      """
      {"title": "T", "head": "edit/one", "base": "main", "body": "B"}
      """
    And the calls recorded are:
      | plugin         | operation     | request_id   | repo |
      | github.edit-pr | open-pr       | from-the-app | o/r  |
      | github.edit-pr | open-pr       | from-the-app | o/r  |
      | github.edit-pr | open-pr       | from-the-app | o/r  |
      | github.edit-pr | open-pr       | from-the-app | o/r  |
      | github.edit-pr | open-pr       | from-the-app | o/r  |
      | github.edit-pr | open-pr       | from-the-app | o/r  |
      | github.edit-pr | open-pr       | from-the-app | o/r  |
      | github.edit-pr | service-total | from-the-app | o/r  |

  Scenario: Parent, base and message are the caller's
    Given the "api.github.com" service answers "GET /repos/o/r/git/commits/read-here" with 200 and:
      """
      {"tree": {"sha": "base-tree"}}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"},
       "base": "dev", "parent": "read-here", "message": "M"}
      """
    Then the answer is 200
    And the "api.github.com" service was asked, in turn:
      """
      GET /repos/o/r/git/commits/read-here
      POST /repos/o/r/git/trees
      POST /repos/o/r/git/commits
      POST /repos/o/r/git/refs
      POST /repos/o/r/pulls
      """
    And the "api.github.com" service was sent:
      """
      {"message": "M", "tree": "next-tree", "parents": ["read-here"]}
      """
    And the "api.github.com" service was sent:
      """
      {"title": "T", "head": "edit/one", "base": "dev", "body": "B"}
      """

  Scenario: No change is nothing to change
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {}}
      """
    Then the answer is the JSON:
      """
      {"status": "nothing to change"}
      """
    And the "api.github.com" service was asked nothing

  Scenario: The same tree is nothing to change
    Given the "api.github.com" service answers "POST /repos/o/r/git/trees" with 201 and:
      """
      {"sha": "base-tree"}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is the JSON:
      """
      {"status": "nothing to change"}
      """
    And the "api.github.com" service was asked "POST /repos/o/r/pulls" 0 times

  Scenario: A file that moved is stale and writes nothing
    Given the "api.github.com" service answers "GET /repos/o/r/contents/a.txt?ref=start" with 200 and:
      """
      {"sha": "moved"}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"},
       "expect": {"a.txt": "read"}}
      """
    Then the answer is the JSON:
      """
      {"status": "stale", "path": "a.txt"}
      """
    And the "api.github.com" service was asked, in turn:
      """
      GET /repos/o/r
      GET /repos/o/r/git/ref/heads/main
      GET /repos/o/r/git/commits/start
      GET /repos/o/r/contents/a.txt?ref=start
      """

  Scenario: A file expected missing that is missing is no conflict
    Given the "api.github.com" service answers "GET /repos/o/r/contents/a.txt?ref=start" with 404 and:
      """
      {}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"},
       "expect": {"a.txt": null}}
      """
    Then the answer is the JSON:
      """
      {"status": "opened", "url": "https://github.com/o/r/pull/7", "number": 7, "branch": "edit/one", "commit": "commit"}
      """

  Scenario: A file that can't be read for expect is refused
    Given the "api.github.com" service answers "GET /repos/o/r/contents/a.txt?ref=start" with 403 and:
      """
      {}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"},
       "expect": {"a.txt": "read"}}
      """
    Then the answer is 403

  Scenario: Deleting skips a file that isn't there
    Given the "api.github.com" service answers "GET /repos/o/r/contents/gone.txt?ref=start" with 404 and:
      """
      {}
      """
    And the "api.github.com" service answers "GET /repos/o/r/contents/there.txt?ref=start" with 200 and:
      """
      {"sha": "s"}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"gone.txt": null, "there.txt": null}}
      """
    Then the answer is the JSON:
      """
      {"status": "opened", "url": "https://github.com/o/r/pull/7", "number": 7, "branch": "edit/one", "commit": "commit"}
      """
    And the "api.github.com" service was sent:
      """
      {"base_tree": "base-tree", "tree": [{"path": "there.txt", "mode": "100644", "type": "blob", "sha": null}]}
      """

  Scenario: Deleting only files that aren't there is nothing to change
    Given the "api.github.com" service answers "GET /repos/o/r/contents/gone.txt?ref=start" with 404 and:
      """
      {}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"gone.txt": null}}
      """
    Then the answer is the JSON:
      """
      {"status": "nothing to change"}
      """

  Scenario Outline: Auto merge says what happened, and the pull request stands
    Given the "api.github.com" service answers "POST /graphql" with <status> and:
      """
      <graphql>
      """
    And the "api.github.com" service answers "PUT /repos/o/r/pulls/7/merge" with 200 and:
      """
      {}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}, "autoMerge": true}
      """
    Then the answer is the JSON:
      """
      {"status": "opened", "url": "https://github.com/o/r/pull/7", "number": 7, "branch": "edit/one", "commit": "commit",
       "autoMerge": "<says>"}
      """

    Examples:
      | status | graphql                                                       | says           |
      | 200    | {"data": {}}                                                  | on             |
      | 200    | {"errors": [{"message": "not allowed"}]}                      | not allowed    |
      | 200    | {"errors": [{"message": "Pull request is in clean status"}]}  | merged         |
      | 403    | {"message": "nope"}                                           | HTTP 403: nope |

  Scenario: A repository not named is refused before any call
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "other/repo", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 403
    And the answer includes "not served"
    And the "api.github.com" service was asked nothing

  Scenario: With nothing named, nothing is served
    Given the service takes its settings from the environment
    And the environment does not set "INTEGRATIONS_EDIT_PR_REPOS"
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 403

  Scenario: Settings come from the environment
    Given the environment sets "INTEGRATIONS_EDIT_PR_REPOS" to "o/r, a/b"
    And the environment sets "INTEGRATIONS_ORIGINS" to "https://site.example"
    And the environment sets "INTEGRATIONS_EDIT_PR_PER_MINUTE" to "5"
    And the environment sets "INTEGRATIONS_EDIT_PR_TIMINGS_PER_MINUTE" to "7"
    When the settings are read from the environment
    Then the result's "edit_pr_repos" is "["a/b", "o/r"]"
    And the result's "allowed_origins" is "["https://site.example"]"
    And the result's "edit_pr_per_minute" is "5"
    And the result's "edit_pr_timings_per_minute" is "7"

  Scenario: No token is refused
    Given GitHub answers the rest of a proposal
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 401
    And the "api.github.com" service was asked nothing

  Scenario Outline: GitHub's refusal comes back with its words
    Given the "api.github.com" service answers "POST /repos/o/r/git/refs" with <status> and:
      """
      {"message": "<message>"}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is <status>
    And the answer is the JSON:
      """
      {"detail": "GitHub said <status>: <message>"}
      """

    Examples:
      | status | message                  |
      | 401    | Bad credentials          |
      | 404    | Not Found                |
      | 422    | Reference already exists |

  Scenario: A refusal with no JSON still says its status
    Given the "api.github.com" service answers "GET /repos/o/r" with 403
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 403

  Scenario: GitHub not answering is a 503
    Given a fake "api.github.com" service
    And "api.github.com" does not answer
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 503

  Scenario: An answer from GitHub that can't be used is a 502
    Given GitHub answers the rest of a proposal
    And GitHub's answer to a proposal cannot be used
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 502

  # The token

  Scenario: The token is in no record, log or answer of a pull request opened
    Given the secret "person-secret-token"
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 200
    And no secret appears in what it produced but what it sent "api.github.com"

  Scenario: The token is in no record, log or answer of a refusal
    Given the secret "person-secret-token"
    And the "api.github.com" service answers "POST /repos/o/r/git/refs" with 401 and:
      """
      {"message": "Bad credentials"}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 401
    And no secret appears in what it produced but what it sent "api.github.com"

  Scenario: The token is in no record, log or answer when GitHub doesn't answer
    Given the secret "person-secret-token"
    And a fake "api.github.com" service
    And "api.github.com" does not answer
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 503
    And no secret appears in what it produced but what it sent "api.github.com"

  Scenario: The token is in no record, log or answer when GitHub answers nothing it was asked
    Given the secret "person-secret-token"
    And a fake "api.github.com" service
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 404
    And no secret appears in what it produced but what it sent "api.github.com"

  Scenario: The token is in no validation error
    Given the secret "person-secret-token"
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r"}
      """
    Then the answer is 422
    And no secret appears in what it produced
    And no secret appears in what it produced but what it sent "api.github.com"

  Scenario: The service keeps no token
    Given the secret "person-secret-token"
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 200
    And the service keeps no secret

  Scenario: A record on stdout holds no token
    Given the secret "person-secret-token"
    And the service writes its call records to stdout
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the service printed ""plugin": "github.edit-pr""
    And no secret appears in what it produced but what it sent "api.github.com"

  # The web

  Scenario: CORS lets a named site propose
    Given requests come from "https://site.example"
    And requests carry the header "Access-Control-Request-Method" set to "POST"
    And requests carry the header "Access-Control-Request-Headers" set to "authorization,content-type"
    When I send "OPTIONS" to "/v1/github/edit-pr/proposals"
    Then the answer's "access-control-allow-origin" is "https://site.example"
    And the answer has no "access-control-allow-credentials"

  Scenario: CORS lets in no other site
    Given requests come from "https://evil.example"
    And requests carry the header "Access-Control-Request-Method" set to "POST"
    And requests carry the header "Access-Control-Request-Headers" set to "authorization,content-type"
    When I send "OPTIONS" to "/v1/github/edit-pr/proposals"
    Then the answer has no "access-control-allow-origin"

  Scenario: CORS lets a named site read
    Given requests come from "https://site.example"
    And requests carry the header "Access-Control-Request-Method" set to "GET"
    And requests carry the header "Access-Control-Request-Headers" set to "authorization"
    When I send "OPTIONS" to "/v1/github/edit-pr/snapshot"
    Then the answer's "access-control-allow-origin" is "https://site.example"

  Scenario: The rate limit is per minute and refuses before any call
    Given the service allows 1 proposals a minute
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    And "POST /v1/github/edit-pr/proposals" was answered 200 to:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 429
    And the "api.github.com" service was asked, in turn:
      """
      GET /repos/o/r
      GET /repos/o/r/git/ref/heads/main
      GET /repos/o/r/git/commits/start
      POST /repos/o/r/git/trees
      POST /repos/o/r/git/commits
      POST /repos/o/r/git/refs
      POST /repos/o/r/pulls
      """

  Scenario: A rate limit lets calls in again after its window
    When a limit of 1 a minute is asked at the seconds "0,0,61"
    Then the result is "[true, false, true]"

  Scenario: A body too large is refused before it is read
    Given the service takes bodies of at most 10 bytes
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 413
    And the "api.github.com" service was asked nothing

  Scenario: A bad proposal is a validation error
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "../../etc", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 422
    And the "api.github.com" service was asked nothing

  Scenario: The address alone leads to the docs
    When I GET "/"
    Then the answer is 307
    And the answer redirects to "/docs/"

  Scenario: A project's proposal is recorded as that project's
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the calls recorded are:
      | repo | operation     |
      | o/r  | open-pr       |
      | o/r  | open-pr       |
      | o/r  | open-pr       |
      | o/r  | open-pr       |
      | o/r  | open-pr       |
      | o/r  | open-pr       |
      | o/r  | open-pr       |
      | o/r  | service-total |

  Scenario: A project's reads are recorded as that project's
    Given GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I GET "/v1/github/edit-pr/snapshot?repo=o/r"
    And I POST to "/v1/github/edit-pr/files" with:
      """
      {"repo": "o/r", "commit": "tip", "paths": ["a.txt"]}
      """
    Then the calls recorded are:
      | repo | operation     |
      | o/r  | read          |
      | o/r  | read          |
      | o/r  | read          |
      | o/r  | service-total |
      | o/r  | read          |
      | o/r  | service-total |

  # Health and docs

  Scenario Outline: Health needs nothing
    When I GET "<path>"
    Then the answer is 200
    And the answer is the JSON:
      """
      <answer>
      """

    Examples:
      | path     | answer                                          |
      | /healthz | {"status": "ok", "unhealthy": [], "alerts": []} |
      | /livez   | {"status": "ok"}                                |

  Scenario: A buffer it cannot reach only alerts
    Given the service's check of "storage.buffer" fails, saying "no token"
    When I GET "/healthz"
    Then the answer is 200
    And the answer is the JSON:
      """
      {"status": "ok", "unhealthy": [], "alerts": ["storage.buffer"]}
      """

  Scenario: The service's storage token is unhealthy when its file can't be read
    Given a scratch directory
    And the environment sets "INTEGRATIONS_STORAGE_URL" to "https://storage.example"
    And the environment sets "INTEGRATIONS_STORAGE_TOKEN_FILE" to "token"
    When the service checks what it needs
    Then the result is:
      | plugin         | ok    | detail                |
      | storage.buffer | false | token can't be read   |

  Scenario: The service's storage token is healthy when its file holds one
    Given a scratch directory
    And a file "token" holding "t"
    And the environment sets "INTEGRATIONS_STORAGE_URL" to "https://storage.example"
    And the environment sets "INTEGRATIONS_STORAGE_TOKEN_FILE" to "token"
    When the service checks what it needs
    Then the result is:
      | plugin         | ok   | detail |
      | storage.buffer | true |        |

  Scenario: With no storage the service checks nothing
    Given a scratch directory
    And a file "token" holding "t"
    And the environment does not set "INTEGRATIONS_STORAGE_URL"
    And the environment sets "INTEGRATIONS_STORAGE_TOKEN_FILE" to "token"
    When the service checks what it needs
    Then the result is empty

  Scenario: The built docs are served
    Given a scratch directory
    And a file "docs/index.html" holding "<title>the docs</title>"
    And the service serves its docs from "docs"
    When I GET "/docs/"
    Then the answer is 200
    And the answer includes "the docs"

  Scenario: The interactive docs are off
    Given a scratch directory
    And a file "docs/index.html" holding "<title>the docs</title>"
    And the service serves its docs from "docs"
    When I GET "/redoc"
    Then the answer is 404

  Scenario: Without built docs there are none
    When I GET "/docs"
    Then the answer is 404

  # Reads

  Scenario: A snapshot is the newest commit, its paths, and whether the token may write
    Given GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    And requests carry the header "X-Request-Id" set to "from-the-app"
    When I GET "/v1/github/edit-pr/snapshot?repo=o/r"
    Then the answer is the JSON:
      """
      {"branch": "main", "commit": "tip", "can_push": true, "paths": ["a.txt", "dir/b.txt"], "truncated": false}
      """
    And every request to the "api.github.com" service carried the header "Authorization" set to "Bearer person-secret-token"
    And the calls recorded are:
      | plugin         | operation     | request_id   |
      | github.edit-pr | read          | from-the-app |
      | github.edit-pr | read          | from-the-app |
      | github.edit-pr | read          | from-the-app |
      | github.edit-pr | service-total | from-the-app |

  Scenario: A snapshot says when the token may not write and when GitHub left paths out
    Given the "api.github.com" service answers "GET /repos/o/r" with 200 and:
      """
      {"default_branch": "main", "permissions": {"push": false}}
      """
    And the "api.github.com" service answers "GET /repos/o/r/git/trees/tip?recursive=1" with 200 and:
      """
      {"tree": [], "truncated": true}
      """
    And GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I GET "/v1/github/edit-pr/snapshot?repo=o/r"
    Then the answer is the JSON:
      """
      {"branch": "main", "commit": "tip", "can_push": false, "paths": [], "truncated": true}
      """

  Scenario: A snapshot can be of another branch
    Given the "api.github.com" service answers "GET /repos/o/r/git/ref/heads/dev" with 200 and:
      """
      {"object": {"sha": "tip"}}
      """
    And GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I GET "/v1/github/edit-pr/snapshot?repo=o/r&branch=dev"
    Then the answer is the JSON:
      """
      {"branch": "dev", "commit": "tip", "can_push": true, "paths": ["a.txt", "dir/b.txt"], "truncated": false}
      """
    And the "api.github.com" service was asked, in turn:
      """
      GET /repos/o/r
      GET /repos/o/r/git/ref/heads/dev
      GET /repos/o/r/git/trees/tip?recursive=1
      """

  Scenario: Files come in one call, with null for one that isn't there
    Given GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/files" with:
      """
      {"repo": "o/r", "commit": "tip", "paths": ["a.txt", "dir/bä.txt", "gone.txt"]}
      """
    Then the answer is the JSON:
      """
      {"files": {"a.txt": "text of a", "dir/bä.txt": "text of bä", "gone.txt": null}}
      """
    And every request to the "api.github.com" service carried the header "Accept" set to "application/vnd.github.raw+json"
    And the "api.github.com" service was asked "GET /repos/o/r/contents/a.txt?ref=tip" 1 times
    And the "api.github.com" service was asked "GET /repos/o/r/contents/dir/b%C3%A4.txt?ref=tip" 1 times
    And the "api.github.com" service was asked "GET /repos/o/r/contents/gone.txt?ref=tip" 1 times
    And the calls recorded are:
      | operation     |
      | read          |
      | read          |
      | read          |
      | service-total |

  Scenario: A file GitHub refuses refuses the call
    Given the "api.github.com" service answers "GET /repos/o/r/contents/a.txt?ref=tip" with 403 and:
      """
      {"message": "no"}
      """
    And GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/files" with:
      """
      {"repo": "o/r", "commit": "tip", "paths": ["a.txt"]}
      """
    Then the answer is 403
    And the answer is the JSON:
      """
      {"detail": "GitHub said 403: no"}
      """

  Scenario: Too many files is a validation error
    Given GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    And a scratch directory
    And a file "files.json" that asks "o/r" for 2001 files
    And requests carry the header "Content-Type" set to "application/json"
    When I send "POST" to "/v1/github/edit-pr/files" with the file "files.json"
    Then the answer is 422
    And the "api.github.com" service was asked nothing

  Scenario Outline: Reads serve only the repositories named
    Given GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I send "<method>" to "<path>" with:
      """
      <body>
      """
    Then the answer is 403
    And the "api.github.com" service was asked nothing

    Examples:
      | method | path                                         | body                                                        |
      | GET    | /v1/github/edit-pr/snapshot?repo=other/repo  | {}                                                          |
      | POST   | /v1/github/edit-pr/files                     | {"repo": "other/repo", "commit": "tip", "paths": ["a"]}     |

  Scenario Outline: Reads need a token
    Given GitHub answers the rest of a read
    When I send "<method>" to "<path>" with:
      """
      <body>
      """
    Then the answer is 401
    And the "api.github.com" service was asked nothing

    Examples:
      | method | path                                         | body                                                        |
      | GET    | /v1/github/edit-pr/snapshot?repo=other/repo  | {}                                                          |
      | POST   | /v1/github/edit-pr/files                     | {"repo": "other/repo", "commit": "tip", "paths": ["a"]}     |

  Scenario: Reads have their own rate limit
    Given the service allows 1 reads a minute
    And GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    And "GET /v1/github/edit-pr/snapshot?repo=o/r" was answered 200
    When I GET "/v1/github/edit-pr/snapshot?repo=o/r"
    Then the answer is 429

  Scenario: Reads don't use up what proposals need
    Given the service allows 1 reads a minute
    And the "api.github.com" service answers "GET /repos/o/r/git/commits/tip" with 200 and:
      """
      {"tree": {"sha": "base-tree"}}
      """
    And GitHub answers the rest of a read
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    And "GET /v1/github/edit-pr/snapshot?repo=o/r" was answered 200
    And "GET /v1/github/edit-pr/snapshot?repo=o/r" was answered 429
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 200

  Scenario Outline: Reads keep the token out of records, logs and answers
    Given the secret "person-secret-token"
    And <github>
    And GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I GET "/v1/github/edit-pr/snapshot?repo=o/r"
    And I POST to "/v1/github/edit-pr/files" with:
      """
      {"repo": "o/r", "commit": "tip", "paths": ["a.txt"]}
      """
    Then the answer is <files>
    And no secret appears in what it produced but what it sent "api.github.com"

    Examples:
      | github                                                       | files |
      | a fake "api.github.com" service                              | 200   |
      | the "api.github.com" service answers "GET /repos/o/r" with 401 | 200   |
      | GitHub does not answer "GET /repos/o/r/contents/a.txt?ref=tip" | 503   |

  # Request ids and timings

  Scenario: Every call of a proposal shares its id, and the answer says it
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer's "X-Request-Id" is a new id
    And every call recorded has the id the answer's "X-Request-Id" gives

  Scenario: Every call of a read shares its id, and the answer says it
    Given GitHub answers the rest of a read
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I GET "/v1/github/edit-pr/snapshot?repo=o/r"
    Then the answer's "X-Request-Id" is a new id
    And every call recorded has the id the answer's "X-Request-Id" gives

  Scenario: An app's own id is kept
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    And requests carry the header "X-Request-Id" set to "from-the-app"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer's "X-Request-Id" is "from-the-app"
    And every call recorded has the id the answer's "X-Request-Id" gives

  Scenario: An app's id that isn't one is replaced
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    And requests carry the header "X-Request-Id" set to "not one	at all"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer's "X-Request-Id" is a new id
    And every call recorded has the id the answer's "X-Request-Id" gives

  Scenario: A request records its whole time as well as its calls
    Given GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the calls recorded are:
      | operation     | route   | status | outcome | repo |
      | open-pr       | routed  | 200    | ok      | o/r  |
      | open-pr       | routed  | 200    | ok      | o/r  |
      | open-pr       | routed  | 200    | ok      | o/r  |
      | open-pr       | routed  | 201    | ok      | o/r  |
      | open-pr       | routed  | 201    | ok      | o/r  |
      | open-pr       | routed  | 201    | ok      | o/r  |
      | open-pr       | routed  | 201    | ok      | o/r  |
      | service-total | none    | 200    | ok      | o/r  |
    And every call recorded has "latency_ms" at least 0

  Scenario: A request GitHub refuses records how it ended
    Given the "api.github.com" service answers "POST /repos/o/r/git/refs" with 422 and:
      """
      {"message": "exists"}
      """
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 422
    And the calls recorded are:
      | operation     | status | outcome |
      | open-pr       | 200    | ok      |
      | open-pr       | 200    | ok      |
      | open-pr       | 200    | ok      |
      | open-pr       | 201    | ok      |
      | open-pr       | 201    | ok      |
      | open-pr       | 422    | refused |
      | service-total | 422    | refused |

  Scenario: A request GitHub doesn't answer records how it ended
    Given GitHub does not answer "GET /repos/o/r"
    And GitHub answers the rest of a proposal
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 503
    And the calls recorded are:
      | operation     | status | outcome     |
      | open-pr       | null   | unavailable |
      | service-total | 503    | unavailable |

  Scenario: An app's timings become records, with no token needed
    When I POST to "/v1/github/edit-pr/timings" with:
      """
      {"repo": "o/r", "timings": [{"request_id": "a-1", "total_ms": 120}, {"request_id": "b-2", "total_ms": 80}]}
      """
    Then the answer is 202
    And the answer is the JSON:
      """
      {"accepted": 2}
      """
    And the calls recorded are:
      | operation    | request_id | latency_ms | repo |
      | client-total | a-1        | 120        | o/r  |
      | client-total | b-2        | 80         | o/r  |

  Scenario Outline: Timings are only ids and sensible numbers
    When I POST to "/v1/github/edit-pr/timings" with:
      """
      {"repo": "o/r", "timings": [<timing>]}
      """
    Then the answer is 422
    And no call was recorded

    Examples:
      | timing                                  |
      | {"request_id": "bad id!", "total_ms": 1} |
      | {"request_id": "a", "total_ms": -1}      |
      | {"request_id": "a", "total_ms": 3600001} |

  Scenario: No more than 200 timings come at once
    Given a scratch directory
    And a file "timings.json" that reports 201 timings for "o/r"
    And requests carry the header "Content-Type" set to "application/json"
    When I send "POST" to "/v1/github/edit-pr/timings" with the file "timings.json"
    Then the answer is 422
    And no call was recorded

  Scenario: Timings serve only the repositories named
    When I POST to "/v1/github/edit-pr/timings" with:
      """
      {"repo": "other/repo", "timings": []}
      """
    Then the answer is 403

  Scenario: Timings are rate limited
    Given the service allows 1 timings a minute
    And "POST /v1/github/edit-pr/timings" was answered 202 to:
      """
      {"repo": "o/r", "timings": []}
      """
    When I POST to "/v1/github/edit-pr/timings" with:
      """
      {"repo": "o/r", "timings": []}
      """
    Then the answer is 429

  Scenario: Timings with no token can't use up what reads need
    Given the service allows 1 timings a minute
    And the service allows 1 reads a minute
    And the service allows 1 proposals a minute
    And GitHub answers the rest of a read
    And "POST /v1/github/edit-pr/timings" was answered 202 to:
      """
      {"repo": "o/r", "timings": []}
      """
    And "POST /v1/github/edit-pr/timings" was answered 429 to:
      """
      {"repo": "o/r", "timings": []}
      """
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I GET "/v1/github/edit-pr/snapshot?repo=o/r"
    Then the answer is 200

  Scenario: Timings with no token can't use up what proposals need
    Given the service allows 1 timings a minute
    And the service allows 1 reads a minute
    And the service allows 1 proposals a minute
    And GitHub answers the rest of a proposal
    And "POST /v1/github/edit-pr/timings" was answered 202 to:
      """
      {"repo": "o/r", "timings": []}
      """
    And "POST /v1/github/edit-pr/timings" was answered 429 to:
      """
      {"repo": "o/r", "timings": []}
      """
    And requests carry the header "Authorization" set to "Bearer person-secret-token"
    When I POST to "/v1/github/edit-pr/proposals" with:
      """
      {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}
      """
    Then the answer is 200

  # The spec and the sink

  Scenario: The committed spec is what the service gives
    Given a scratch directory
    And a file "integrations.json" copied from "openapi/integrations.json"
    When the service's spec is made
    Then the result is what the file "integrations.json" holds

  Scenario: Records go to the log when no deployment names storage
    Given the environment does not set "INTEGRATIONS_STORAGE_URL"
    When the service makes its sink
    Then the result is:
      """
      {"kind": "StdoutSink"}
      """

  Scenario: Records go to storage when a deployment names it
    Given a scratch directory
    And a file "token" holding "pod-token"
    And the environment sets "INTEGRATIONS_STORAGE_URL" to "https://storage.example/"
    And the environment sets "INTEGRATIONS_STORAGE_TOKEN_FILE" to "token"
    And the environment sets "INGEST_DATASETS" to "{"o/r": "calls_r"}"
    And the environment sets "HOSTNAME" to "app-1"
    When the service makes its sink
    Then the result is:
      """
      {"kind": "StorageSink", "base": "https://storage.example", "sender": "app-1", "token": "pod-token",
       "datasets": {"o/r": "calls_r"}}
      """
