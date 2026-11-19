Feature: The storage client (integrations.clients.storage), on the vectors in tests/vectors/storage/, and what they don't cover

  Scenario Outline: A vector
    Given the storage vector "<vector>"
    When I make its call on its recorded answers
    Then it does what the vector expects

    Examples:
      | vector                |
      | buffer-unavailable    |
      | delete-buffer         |
      | get-file-missing      |
      | get-file              |
      | get-head              |
      | head-log              |
      | list-buffer           |
      | list-files-by-subject |
      | move-head-first       |
      | move-head-lost        |
      | put-buffer            |
      | request-id            |
      | sink-once             |
      | upload-file           |

  Scenario: Every storage vector is run
    When I list the storage vectors
    Then the result is:
      """
      ["buffer-unavailable", "delete-buffer", "get-file-missing", "get-file", "get-head", "head-log",
       "list-buffer", "list-files-by-subject", "move-head-first", "move-head-lost", "put-buffer",
       "request-id", "sink-once", "upload-file"]
      """

  Scenario: The routes are the pinned spec's
    When I compare the storage routes' spec with "openapi/storage.lock.json"
    Then the result is empty

  Scenario: A listing's query is the spec's
    When I read the storage route "list"
    Then the result's "query" is "["prefix", "limit", "cursor"]"

  Scenario: An upload is one multipart body with the fields given
    Given a storage client for the tenant "deals" with the token "t"
    And the "storage.example" service answers "POST /deals/files" with 200 and:
      """
      {}
      """
    When I upload "%PDF-1.7" as "a.pdf", not kept, for "someone"
    Then the "storage.example" service was asked "POST /deals/files" 1 times
    And the "storage.example" service was sent the form:
      | field | filename | type                     | value    |
      | file  | a.pdf    | application/octet-stream | %PDF-1.7 |
      | name  |          |                          | a.pdf    |
      | kept  |          |                          | false    |
      | for   |          |                          | someone  |

  Scenario: The token file is read for each request, and another tenant shares it
    Given a scratch directory
    And a file "token" holding "first"
    And a storage client for the tenant "deals" with the token file "token"
    And the "storage.example" service answers "GET /deals/head/board/log" with 200 and:
      """
      {"log": []}
      """
    And the "storage.example" service answers "GET /other/head/board/log" with 200 and:
      """
      {"log": []}
      """
    When I read the log of the head "board"
    And the file "token" changes to:
      """
      second
      """
    And I read the log of the head "board" as the tenant "other"
    Then the "storage.example" service was asked "GET /deals/head/board/log" with the header "Authorization" set to "Bearer first"
    And the "storage.example" service was asked "GET /other/head/board/log" with the header "Authorization" set to "Bearer second"
    And the "storage.example" service was asked "GET /deals/head/board/log" 1 times
    And the "storage.example" service was asked "GET /other/head/board/log" 1 times

  Scenario: A token can be a function
    Given a storage client for the tenant "deals" whose token is a function giving "minted"
    And the "storage.example" service answers "GET /deals/head/board/log" with 200 and:
      """
      {"log": []}
      """
    When I read the log of the head "board"
    Then the "storage.example" service was asked "GET /deals/head/board/log" with the header "Authorization" set to "Bearer minted"

  Scenario: A storage client needs a token
    When I make a storage client for the tenant "deals" with no token
    Then it is refused, saying "a Storage needs a token or a token_file"

  Scenario Outline: A place outside the buffers' folders is refused here
    Given a storage client for the tenant "deals" with the token "t"
    When I get the buffer file "<place>"
    Then it is refused, saying "<place> is not in inbox/, batches/ or rejected/"
    And the "storage.example" service was asked nothing

    Examples:
      | place        |
      | f/x          |
      | inbox        |
      | other/a.avro |

  Scenario Outline: Errors say storage's reason and are the caller's kinds
    When I make the storage error <kind> of "<operation>" with HTTP <status> and the reason "<reason>"
    Then the result's "says" is "<says>"
    And the result's "is a StorageError" is "true"

    Examples:
      | kind         | operation | status | reason    | says                                |
      | NotFound     | read      | 404    | not found | storage.api read: HTTP 404 not found |
      | StorageError | write     | 500    |           | storage.api write: HTTP 500          |

  Scenario: No answer has no reason
    When I read storage's reason in no answer
    Then the result is empty

  Scenario: An answer that is not JSON has no reason
    When I read storage's reason in an answer of 500 with the body "<html>"
    Then the result is empty

  Scenario: A weak ETag still gives the hash
    Given a storage client for the tenant "deals" with the token "t"
    And the "storage.example" service answers "GET /deals/head/board" with 200, the header "ETag" set to "W/"abc"", and:
      """
      {}
      """
    When I get the head "board"
    Then the result is:
      """
      {"body": {"text": "{}"}, "hash": "abc"}
      """
