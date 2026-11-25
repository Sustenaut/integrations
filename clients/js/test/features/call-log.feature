@call-log
Feature: workers/call-log's Durable Object, on fakes of its storage and R2, and caller.js's CallLogSink that sends it records
  The object's Durable Object storage is kept in memory, its clock starts at
  2027-01-15T08:00:00Z, and its R2 bucket keeps files in the scratch directory.
  Records are named by their request ids: "r1,r2" is two full call records.

  Background:
    Given a scratch directory
    And a call-log object whose senders are:
      """
      {"storage": "calls_storage", "other": "calls_other"}
      """

  Scenario: Records are kept the moment they come, and the first sets the two-minute alarm
    When "storage" sends the records "r1,r2"
    Then the result is:
      """
      {"status": 200, "body": {"kept": 2}}
      """
    And the alarm is set for 120 s after the start
    And the object keeps the records "r1,r2"

  Scenario: A set alarm isn't moved
    When "storage" sends the records "r1,r2"
    And a minute passes
    And "storage" sends the records "r3"
    Then the alarm is set for 120 s after the start
    And the object keeps the records "r1,r2,r3"

  Scenario: The alarm writes one file of the cycle's records into the sender's buffer, and re-arms
    When "storage" sends the records "r1,r2"
    And two minutes pass and the alarm rings
    Then the directory "calls-storage/inbox/calls/2027-01-15" holds:
      """
      080200-storage-4fbcdabd6d1ff490.avro
      """
    And the report in "calls-storage/inbox/calls/2027-01-15/080200-storage-4fbcdabd6d1ff490.avro" is:
      """
      {"kind": "sender", "sender": "storage", "version": "call-log", "at": "2027-01-15T08:02:00Z",
       "cycle_start": "2027-01-15T08:00:00Z", "cycle_end": "2027-01-15T08:02:00Z", "calls": 2, "drops": 0,
       "buffer_size": 2, "storage_failures": 0}
      """
    And the object keeps the records ""
    And the alarm is set for 240 s after the start

  Scenario: An idle cycle still writes a report, so a quiet sender can be told from a stopped one
    When "storage" sends the records ""
    And two minutes pass and the alarm rings
    And two minutes pass and the alarm rings
    And two minutes pass and the alarm rings
    Then the directory "calls-storage/inbox/calls/2027-01-15" holds:
      """
      080200-storage-d52175ef7bb675ea.avro
      080400-storage-caec86ad7a9b37f9.avro
      080600-storage-0e38f097109730d6.avro
      """
    And the report in "calls-storage/inbox/calls/2027-01-15/080200-storage-d52175ef7bb675ea.avro" has "calls" equal to "0"
    And the report in "calls-storage/inbox/calls/2027-01-15/080400-storage-caec86ad7a9b37f9.avro" has "calls" equal to "0"
    And the report in "calls-storage/inbox/calls/2027-01-15/080600-storage-0e38f097109730d6.avro" has "calls" equal to "0"

  Scenario: A file R2 didn't take goes again as it was, under its name, and nothing is sent twice
    Given R2 is down
    When "storage" sends the records "r1"
    And two minutes pass and the alarm rings
    And "storage" sends the records "r2"
    And two minutes pass and the alarm rings
    Then the file "calls-storage" does not exist
    And stderr mentions "call-log: R2 didn't take inbox/calls/2027-01-15/080200-storage-b54b25193a65b295.avro for calls-storage: R2 is down"

  Scenario: A held file goes first, then the next with the failures counted
    Given R2 is down
    When "storage" sends the records "r1"
    And two minutes pass and the alarm rings
    And "storage" sends the records "r2"
    And two minutes pass and the alarm rings
    And R2 comes back
    And two minutes pass and the alarm rings
    Then the directory "calls-storage/inbox/calls/2027-01-15" holds:
      """
      080200-storage-b54b25193a65b295.avro
      080600-storage-043186d50a43d6fa.avro
      """
    And the report in "calls-storage/inbox/calls/2027-01-15/080200-storage-b54b25193a65b295.avro" has "calls" equal to "1"
    And the report in "calls-storage/inbox/calls/2027-01-15/080200-storage-b54b25193a65b295.avro" has "storage_failures" equal to "0"
    And the report in "calls-storage/inbox/calls/2027-01-15/080600-storage-043186d50a43d6fa.avro" has "calls" equal to "1"
    And the report in "calls-storage/inbox/calls/2027-01-15/080600-storage-043186d50a43d6fa.avro" has "storage_failures" equal to "2"

  Scenario: Past the limit the oldest records go and are counted
    When "storage" sends 10003 records
    And two minutes pass and the alarm rings
    Then the directory "calls-storage/inbox/calls/2027-01-15" holds:
      """
      080200-storage-ba7c33c40e7fab08.avro
      """
    And the report in "calls-storage/inbox/calls/2027-01-15/080200-storage-ba7c33c40e7fab08.avro" has "calls" equal to "10000"
    And the report in "calls-storage/inbox/calls/2027-01-15/080200-storage-ba7c33c40e7fab08.avro" has "drops" equal to "3"
    And the report in "calls-storage/inbox/calls/2027-01-15/080200-storage-ba7c33c40e7fab08.avro" has "buffer_size" equal to "10000"

  Scenario Outline: Only a sender SENDERS names may use an instance
    Given the service is the call-log object
    When I POST to "/records" with:
      """
      {"sender": "<sender>", "records": []}
      """
    Then the answer is <status>

    Examples:
      | sender   | status |
      | stranger | 403    |
      | toString | 403    |
      | storage  | 200    |

  Scenario: An instance is one sender's
    Given the service is the call-log object
    When I POST to "/records" with:
      """
      {"sender": "storage", "records": []}
      """
    And I POST to "/records" with:
      """
      {"sender": "other", "records": []}
      """
    Then the answer is 409
    And the answer is the JSON:
      """
      {"error": "this instance is storage's"}
      """

  Scenario Outline: A post that isn't a list of records is refused
    Given the service is the call-log object
    When I POST to "/records" with:
      """
      <body>
      """
    Then the answer is 400

    Examples:
      | body                                |
      | {"sender": "storage", "records": "x"} |
      | {                                   |

  Scenario: The object answers only a POST of records
    Given the service is the call-log object
    When I GET "/records"
    Then the answer is 404

  Scenario: An instance nobody has sent to does nothing on an alarm, but re-arms, harmlessly
    When the alarm rings
    Then the file "calls-storage" does not exist
    And the alarm is set for 120 s after the start

  Scenario: The Worker answers nothing itself
    Given the service is the call-log Worker
    When I GET "/"
    Then the answer is 404

  Scenario: The report is where health checks and statistics can join, as StorageSink's is
    When I make "s"'s report of the records "r1", two minutes after the start, with the counters:
      """
      {"started": 1800000000000, "drops": 1, "peak": 4, "failures": 2}
      """
    Then the result is:
      """
      {"kind": "sender", "sender": "s", "version": "call-log", "at": "2027-01-15T08:02:00Z",
       "cycle_start": "2027-01-15T08:00:00Z", "cycle_end": "2027-01-15T08:02:00Z", "calls": 1, "drops": 1,
       "buffer_size": 4, "storage_failures": 2}
      """

  Scenario: A sink keeps a request's records and sends them to its sender's object in one call
    Given a CallLogSink for "storage", bound to the object
    When it writes the records "r1,r2" and sends twice
    Then the result is empty
    And it prints:
      """
      the binding gave the object of storage
      """
    And the object keeps the records "r1,r2"

  Scenario: A sink the call log refuses says so and lets the records go, never failing the request
    Given a CallLogSink for "stranger", bound to an object that answers 403 with:
      """
      {"error": "stranger is not a sender here"}
      """
    When it writes the records "r1" and sends
    Then the result is empty
    And stderr mentions "integrations: the call log didn't take 1 records from stranger: HTTP 403 stranger is not a sender here"

  Scenario: A sink with no binding says so and lets the records go, never failing the request
    Given a CallLogSink for "storage", bound to nothing, saying "no binding"
    When it writes the records "r1" and sends
    Then the result is empty
    And stderr mentions "integrations: the call log didn't take 1 records from storage: no binding"

  Scenario: A CallLogSink needs a sender and a namespace
    When I make a "CallLogSink" from "caller.js" of:
      """
      [{"sender": "storage"}]
      """
    Then it is refused, saying "needs a sender and a namespace"

  Scenario: With a deadline, the first record sets the alarm for the deadline
    Given the object's vars are:
      """
      {"CALL_LOG_DEADLINE_SECONDS": "30", "CALL_LOG_HEARTBEAT_SECONDS": "28800"}
      """
    When "storage" sends the records "r1"
    And 10 seconds pass, the alarm silent
    And "storage" sends the records "r2"
    Then the alarm is set for 30 s after the start

  Scenario: With a deadline, the file goes at the deadline and the alarm waits for the heartbeat
    Given the object's vars are:
      """
      {"CALL_LOG_DEADLINE_SECONDS": "30", "CALL_LOG_HEARTBEAT_SECONDS": "28800"}
      """
    When "storage" sends the records "r1,r2"
    And 30 seconds pass and the alarm rings
    Then the directory "calls-storage/inbox/calls/2027-01-15" holds:
      """
      080030-storage-467066caa239a044.avro
      """
    And the report in "calls-storage/inbox/calls/2027-01-15/080030-storage-467066caa239a044.avro" has "calls" equal to "2"
    And the alarm is set for 28830 s after the start

  Scenario: With a deadline, a record after a file brings the alarm forward
    Given the object's vars are:
      """
      {"CALL_LOG_DEADLINE_SECONDS": "30", "CALL_LOG_HEARTBEAT_SECONDS": "28800"}
      """
    When "storage" sends the records "r1"
    And 30 seconds pass and the alarm rings
    And 100 seconds pass, the alarm silent
    And "storage" sends the records "r2"
    Then the alarm is set for 160 s after the start

  Scenario: With a deadline, an idle object writes nothing until its heartbeat
    Given the object's vars are:
      """
      {"CALL_LOG_HEARTBEAT_SECONDS": "600"}
      """
    When "storage" sends the records ""
    Then the alarm is set for 600 s after the start
    When 300 seconds pass and the alarm rings
    Then the file "calls-storage" does not exist
    And the alarm is set for 600 s after the start
    When 300 seconds pass and the alarm rings
    Then the directory "calls-storage/inbox/calls/2027-01-15" holds:
      """
      081000-storage-ca8a6adee4616483.avro
      """
    And the report in "calls-storage/inbox/calls/2027-01-15/081000-storage-ca8a6adee4616483.avro" has "calls" equal to "0"
    And the alarm is set for 1200 s after the start

  Scenario: With a deadline, a file R2 didn't take goes again a deadline later
    Given the object's vars are:
      """
      {"CALL_LOG_DEADLINE_SECONDS": "30", "CALL_LOG_HEARTBEAT_SECONDS": "28800"}
      """
    And R2 is down
    When "storage" sends the records "r1"
    And 30 seconds pass and the alarm rings
    Then the alarm is set for 60 s after the start

  Scenario: Each file R2 takes is told to the ingest pod, signed with the key
    Given the object's vars are:
      """
      {"INGEST_NOTIFY_KEY": "k1"}
      """
    When "storage" sends the records "r1"
    And two minutes pass and the alarm rings
    Then ingest was told, with the key "k1", of:
      """
      [{"url": "https://integrations.sustenaut.eu/ingest/notify", "tenant": "calls-storage",
        "name": "inbox/calls/2027-01-15/080200-storage.avro", "sized": true, "at": "1800000120", "signed": true}]
      """

  Scenario: The notices go where INGEST_NOTIFY_URL says
    Given the object's vars are:
      """
      {"INGEST_NOTIFY_KEY": "k1", "INGEST_NOTIFY_URL": "https://ingest.example/ingest/notify"}
      """
    When "storage" sends the records "r1"
    And two minutes pass and the alarm rings
    Then ingest was told, with the key "k1", of:
      """
      [{"url": "https://ingest.example/ingest/notify", "tenant": "calls-storage",
        "name": "inbox/calls/2027-01-15/080200-storage.avro", "sized": true, "at": "1800000120", "signed": true}]
      """

  Scenario: Without the key the object tells no one
    Given the object's vars are:
      """
      {}
      """
    When "storage" sends the records "r1"
    And two minutes pass and the alarm rings
    Then ingest was told of nothing
    And the object keeps the records ""

  Scenario: Ingest being down loses no file
    Given the object's vars are:
      """
      {"INGEST_NOTIFY_KEY": "k1"}
      """
    And ingest is down
    When "storage" sends the records "r1"
    And two minutes pass and the alarm rings
    Then the directory "calls-storage/inbox/calls/2027-01-15" holds:
      """
      080200-storage-b54b25193a65b295.avro
      """
    And the object keeps the records ""

  Scenario: With a deadline and no heartbeat, the object sets no alarm while nothing is kept
    Given the object's vars are:
      """
      {"CALL_LOG_DEADLINE_SECONDS": "30"}
      """
    When "storage" sends the records ""
    Then no alarm is set

  Scenario: With a deadline and no heartbeat, a file goes at the deadline, and then no alarm until a record comes
    Given the object's vars are:
      """
      {"CALL_LOG_DEADLINE_SECONDS": "30"}
      """
    When "storage" sends the records "r1"
    And 30 seconds pass and the alarm rings
    Then the directory "calls-storage/inbox/calls/2027-01-15" holds:
      """
      080030-storage-76fb94eeee0959be.avro
      """
    And the report in "calls-storage/inbox/calls/2027-01-15/080030-storage-76fb94eeee0959be.avro" has "calls" equal to "1"
    And no alarm is set
    When 600 seconds pass, the alarm silent
    And "storage" sends the records "r2"
    Then the alarm is set for 660 s after the start

  Scenario: With a deadline and no heartbeat, a file R2 didn't take still goes again a deadline later
    Given the object's vars are:
      """
      {"CALL_LOG_DEADLINE_SECONDS": "30"}
      """
    And R2 is down
    When "storage" sends the records "r1"
    And 30 seconds pass and the alarm rings
    Then the alarm is set for 60 s after the start
