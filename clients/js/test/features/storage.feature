Feature: clients/js/storage.js, on the vectors in tests/vectors/storage/ that tests/steps/test_storage_client.py runs too, and what they don't cover
  The client's caller sends https://storage.example to pubtest's fake service of that
  name, unless a Given makes its fetch a stand-in, which answers 200 and says on stdout,
  as a line of JSON, what it was given.

  Scenario Outline: storage.js gives the vector what it expects
    Given the storage call in "tests/vectors/storage/<vector>"
    When storage.js makes it
    Then the result is the JSON at "expect" in "tests/vectors/storage/<vector>"

    Examples:
      | vector                     |
      | buffer-unavailable.json    |
      | delete-buffer.json         |
      | get-file-missing.json      |
      | get-file.json              |
      | get-head.json              |
      | head-log.json              |
      | list-buffer.json           |
      | list-files-by-subject.json |
      | move-head-first.json       |
      | move-head-lost.json        |
      | put-buffer.json            |
      | request-id.json            |
      | sink-once.json             |
      | upload-file.json           |

  Scenario: Every vector in tests/vectors/storage/ is run, and there are vectors to run
    When I list the vectors in "tests/vectors/storage"
    Then the result is:
      """
      ["buffer-unavailable.json", "delete-buffer.json", "get-file-missing.json", "get-file.json", "get-head.json",
       "head-log.json", "list-buffer.json", "list-files-by-subject.json", "move-head-first.json", "move-head-lost.json",
       "put-buffer.json", "request-id.json", "sink-once.json", "upload-file.json"]
      """

  Scenario: The routes are the pinned spec's
    When I compare storage.js's SPEC with "openapi/storage.lock.json"
    Then the result is empty

  Scenario: A list's query is the spec's
    When I look at "ROUTES" from "storage.js"
    Then the result's "list.query" is "[\"prefix\", \"limit\", \"cursor\"]"

  Scenario: The routes can't be changed
    When I ask whether "ROUTES" from "storage.js" is frozen
    Then the result is "true"

  Scenario: An upload is one form with the fields given
    Given a storage client for the tenant "deals" with the token "t"
    And its fetch is a stand-in
    When it uploads "%PDF-1.7" as:
      """
      {"name": "a.pdf", "kept": false, "for": "someone"}
      """
    Then it prints the JSON:
      """
      {"url": "https://storage.example/deals/files", "method": "POST",
       "headers": {"User-Agent": "integrations-caller.js", "Authorization": "Bearer t"},
       "body": {"file": {"name": "a.pdf", "text": "%PDF-1.7"}, "name": "a.pdf", "kept": "false", "for": "someone"},
       "signal": "live"}
      """

  Scenario: An upload needs the file's name
    Given a storage client for the tenant "deals" with the token "t"
    When it calls "uploadFile" with:
      """
      ["bytes with no name"]
      """
    Then it is refused, saying "needs the file's name"

  Scenario: The token file is read for each request, and another tenant shares it
    Given a fake "storage.example" service
    And the "storage.example" service answers "GET /deals/head/board/log" with 200 and:
      """
      {"log": []}
      """
    And the "storage.example" service answers "GET /other/head/board/log" with 200 and:
      """
      {"log": []}
      """
    And a storage client for the tenant "deals" whose token file "/var/run/token" holds "first", then "second"
    When it calls "headLog" with:
      """
      ["board"]
      """
    And its client for the tenant "other" calls "headLog" with:
      """
      ["board"]
      """
    Then the "storage.example" service was asked "GET /deals/head/board/log" with the header "Authorization" set to "Bearer first"
    And the "storage.example" service was asked "GET /other/head/board/log" with the header "Authorization" set to "Bearer second"
    And it prints "read /var/run/token utf8"

  Scenario: A token can be a function
    Given a fake "storage.example" service
    And the "storage.example" service answers "GET /deals/head/board/log" with 200 and:
      """
      {"log": []}
      """
    And a storage client for the tenant "deals" whose token a function gives as "minted"
    When it calls "headLog" with:
      """
      ["board"]
      """
    Then the result is empty
    And the "storage.example" service was asked "GET /deals/head/board/log" with the header "Authorization" set to "Bearer minted"

  Scenario Outline: A storage client needs a token, and a tenant
    When I make a "Storage" from "storage.js" of:
      """
      [<options>]
      """
    Then it is refused, saying "<says>"

    Examples:
      | options             | says                |
      | {"tenant": "deals"} | a token or a tokenFile |
      | {"token": "t"}      | needs a tenant      |

  Scenario: Storage is storage.sustenaut.eu unless the client says otherwise
    When I make a "Storage" from "storage.js" of:
      """
      [{"tenant": "d", "token": "t"}]
      """
    Then the result's "baseUrl" is "https://storage.sustenaut.eu"

  Scenario Outline: A place outside the buffers' folders is refused here
    Given a fake "storage.example" service
    And a storage client for the tenant "deals" with the token "t"
    When it calls "getBuffer" with:
      """
      ["<place>"]
      """
    Then it is refused, saying "is not in inbox/, batches/ or rejected/"
    And the "storage.example" service was asked nothing

    Examples:
      | place        |
      | f/x          |
      | inbox        |
      | other/a.avro |

  Scenario: A batch's JSON goes as it is
    Given a storage client for the tenant "deals" with the token "t"
    And its fetch is a stand-in
    When it calls "putBuffer" with:
      """
      ["batches/job.json", "{\"files\":[]}"]
      """
    Then the JSON it prints has "body" equal to "{\"files\":[]}"
    And the JSON it prints has "method" equal to "PUT"
    And the JSON it prints has "url" equal to "https://storage.example/deals/batches/job.json"

  Scenario: A batch reads back as its JSON
    Given a fake "storage.example" service
    And the "storage.example" service answers "GET /deals/batches/job.json" with 200 and:
      """
      {"files": []}
      """
    And a storage client for the tenant "deals" with the token "t"
    When it calls "getBuffer" with:
      """
      ["batches/job.json"]
      """
    Then the result is:
      """
      {"files": []}
      """

  Scenario Outline: The errors are the caller's kinds
    When I ask whether "<error>" from "storage.js" extends "<kind>" from "<from>"
    Then the result is "true"

    Examples:
      | error              | kind         | from       |
      | NotFound           | StorageError | storage.js |
      | NotFound           | Refused      | caller.js  |
      | StorageUnavailable | Unavailable  | caller.js  |

  Scenario Outline: An error says storage's reason, or else its status
    When I make a "<error>" from "storage.js" of:
      """
      <args>
      """
    Then the result's "message" is "<message>"

    Examples:
      | error        | args                         | message                              |
      | NotFound     | ["read", 404, "not found"]   | storage.api read: HTTP 404 not found |
      | StorageError | ["write", 500, ""]           | storage.api write: HTTP 500          |
