Feature: A sender's files: one every cycle, even with nothing in it, and nothing lost or doubled

  The sink writes into an in-memory storage buffer that lists, takes and gives files as
  the storage service does. What a sink's action comes to is that buffer's inbox: how
  many files it holds, and each file's name, its records' request ids and its report;
  and the sink's own state: the datasets it holds a file back for, its failures, the
  records waiting, and the tenants storage was asked for.

  Background:
    Given the clock reads 2027-01-15T08:00:00Z

  Scenario: An idle cycle still writes a report
    Given the package's version is "1.0"
    And a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    When 120 seconds pass
    And the sink flushes
    Then the result's "count" is "1"
    And the result's "files.0.name" matches "inbox/calls/2027-01-15/080200-app-1-x-*.avro"
    And the result's "files.0.request_ids" is "[]"
    And the result's "files.0.report" is "{"kind": "sender", "sender": "app-1_x", "version": "1.0", "at": "2027-01-15T08:02:00Z", "cycle_start": "2027-01-15T08:00:00Z", "cycle_end": "2027-01-15T08:02:00Z", "calls": 0, "drops": 0, "buffer_size": 0, "storage_failures": 0, "page_count": 0, "page_ms_low": null, "page_ms_median": null, "page_ms_high": null, "cold_start": true}"

  Scenario: A report names the package's version
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    When the sink flushes
    Then the result's "files.0.report.version" is the package's version

  Scenario: A cycle's calls are one file
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given call 1 of "a-repo"
    And the sink was given call 2 of "b-repo"
    When 120 seconds pass
    And the sink flushes
    Then the result's "count" is "1"
    And the result's "files.0.request_ids" is "["r1", "r2"]"
    And the result's "files.0.report.calls" is "2"
    And the result's "files.0.report.buffer_size" is "2"

  Scenario: The next cycle's file starts where the last ended
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given call 1 of "a-repo"
    And the sink was given call 2 of "b-repo"
    And 120 seconds pass
    And the sink flushed
    When 120 seconds pass
    And the sink flushes
    Then the result's "count" is "2"
    And the result's "files.1.report.calls" is "0"
    And the result's "files.1.report.cycle_start" is "2027-01-15T08:02:00Z"

  Scenario: Each project has its own tenant
    Given a sink for the sender "s" sending "a" to "calls_one" and "b" to "calls_two"
    When the sink flushes
    Then the result's "tenants" is "["calls-one", "calls-two"]"

  Scenario: Past the limit the oldest go and are counted
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given calls 1 to 5 of "a-repo"
    When the sink flushes
    Then the result's "files.0.request_ids" is "["r3", "r4", "r5"]"
    And the result's "files.0.report.drops" is "2"
    And the result's "files.0.report.buffer_size" is "3"

  Scenario: A record for a project with no buffer is counted
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given call 1 of "elsewhere"
    When the sink flushes
    Then the result's "files.0.report.drops" is "1"

  Scenario: Writing a record never fails with no projects
    Given a sink for the sender "s" sending nothing
    When the sink is given call 1 of "a-repo"
    And the sink flushes
    Then it is not refused

  Scenario: A file storage refused is held back and the failure counted
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given call 1 of "a-repo"
    And storage takes no PUT
    When 120 seconds pass
    And the sink flushes
    Then the result's "count" is "0"
    And the result's "held" is "["ds"]"
    And the result's "failures.ds" is "1"

  Scenario: While storage refuses, the failures add up
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given call 1 of "a-repo"
    And storage takes no PUT
    And 120 seconds pass
    And the sink flushed
    And the sink was given call 2 of "a-repo"
    When 120 seconds pass
    And the sink flushes
    Then the result's "count" is "0"
    And the result's "failures.ds" is "2"

  Scenario: A refused file goes again as it was, and the failures are reported
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given call 1 of "a-repo"
    And storage takes no PUT
    And 120 seconds pass
    And the sink flushed
    And the sink was given call 2 of "a-repo"
    And 120 seconds pass
    And the sink flushed
    And storage takes files again
    When 120 seconds pass
    And the sink flushes
    Then the file held back went as it was
    And the result's "held" is "[]"
    And the result's "count" is "2"
    And the result's "files.0.request_ids" is "["r1"]"
    And the result's "files.1.request_ids" is "["r2"]"
    And the result's "files.1.report.storage_failures" is "2"

  Scenario: A lost answer can't double a file's calls
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And storage loses its answer to the first PUT
    And the sink was given call 1 of "a-repo"
    And 120 seconds pass
    And the sink flushed
    When 120 seconds pass
    And the sink flushes
    Then the result's "inbox request_ids" is "["r1"]"

  Scenario: A token that can't be read is a failed write
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink's token can't be read, saying "gone"
    And storage answers every request with 401 and the body "{}"
    When the sink flushes
    Then the result's "failures.ds" is "1"
    And the result's "held" is "["ds"]"

  Scenario: From a call to the tables
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given call 1 of "a-repo"
    And 120 seconds pass
    And the sink flushed
    When the scanner scans "ds"
    Then the result's "rows" is "["r1"]"
    And the result's "status rows" is "[{"kind": "sender", "sender": "app-1_x", "calls": 1}]"
    And the result's "inbox" is "[]"

  Scenario: With flush on write, a write before the cycle ends writes nothing
    Given a sink for the sender "run-1" sending "a-repo" to "ds", flushing on write
    When the sink is given call 1 of "a-repo"
    Then the result's "count" is "0"

  Scenario: With flush on write, a write a second before the cycle ends writes nothing
    Given a sink for the sender "run-1" sending "a-repo" to "ds", flushing on write
    And the sink was given call 1 of "a-repo"
    And 119 seconds pass
    When the sink is given call 2 of "a-repo"
    Then the result's "count" is "0"

  Scenario: With flush on write, a request that finds the file overdue writes it
    Given a sink for the sender "run-1" sending "a-repo" to "ds", flushing on write
    And the sink was given call 1 of "a-repo"
    And 119 seconds pass
    And the sink was given call 2 of "a-repo"
    And 1 seconds pass
    When the sink is given call 3 of "a-repo"
    Then the result's "count" is "1"
    And the result's "files.0.request_ids" is "["r1", "r2", "r3"]"
    And the result's "files.0.report.cycle_start" is "2027-01-15T08:00:00Z"

  Scenario: Without it, writing never waits on storage
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And 600 seconds pass
    When the sink is given call 1 of "a-repo"
    Then the result's "count" is "0"

  Scenario: A write doesn't flush while another flush is under way
    Given 200 seconds pass
    And a sink for the sender "run-1" sending "a-repo" to "ds", flushing on write, started 200 seconds ago
    And another flush is under way
    When the sink is given call 1 of "a-repo"
    Then the result's "count" is "0"
    And the result's "waiting.ds" is "1"

  Scenario: A write flushes once the other flush is over
    Given 200 seconds pass
    And a sink for the sender "run-1" sending "a-repo" to "ds", flushing on write, started 200 seconds ago
    And another flush is under way
    And the sink was given call 1 of "a-repo"
    And the other flush is over
    When the sink is given call 2 of "a-repo"
    Then the result's "count" is "1"

  Scenario: A write storage didn't take is said in the log
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And storage takes no PUT
    When the sink flushes
    Then stderr mentions "storage didn't take inbox/calls/"

  Scenario: A refusal says storage's reason, and a missing token says why
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And storage answers every request with 401 and the body "{"error": "refused: a token for another audience"}"
    And the sink's token can't be read, saying "metadata server: timed out"
    When the sink flushes
    Then stderr mentions "HTTP 401 refused: a token for another audience"
    And stderr mentions "no token to send: metadata server: timed out"

  Scenario: A refusal without a reason still says its status
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And storage answers every request with 500 and the body "not json"
    When the sink flushes
    Then stderr has a line matching "*: HTTP 500"

  Scenario: Page times become the window's low, median and high
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given the page times 120.4, 80, 3400, 95.6 and 101
    When 120 seconds pass
    And the sink flushes
    Then the result's "files.0.report.page_count" is "5"
    And the result's "files.0.report.page_ms_low" is "80"
    And the result's "files.0.report.page_ms_median" is "101"
    And the result's "files.0.report.page_ms_high" is "3400"

  Scenario: Only the first window is a cold start, and a quiet one has no times
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given the page times 50
    And 120 seconds pass
    And the sink flushed
    When 120 seconds pass
    And the sink flushes
    Then the result's "files.0.report.cold_start" is "true"
    And the result's "files.1.report.cold_start" is "false"
    And the result's "files.1.report.page_count" is "0"
    And the result's "files.1.report.page_ms_median" is "null"

  Scenario: Past the limit page times are still counted
    Given a sink keeps up to 3 page times
    And a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given the page times 10, 20, 30, 40 and 50
    When the sink flushes
    Then the result's "files.0.report.page_count" is "5"
    And the result's "files.0.report.page_ms_high" is "30"

  Scenario: Page times reach the status table
    Given a sink for the sender "app-1_x" sending "a-repo" and "b-repo" to "ds", keeping 3 calls
    And the sink was given the page times 250
    And 120 seconds pass
    And the sink flushed
    When the scanner scans "ds"
    Then the result's "sender reports" is "[{"page_count": 1, "page_ms_median": 250, "cold_start": true}]"

  Scenario: The thread's first file comes a random part of a cycle in
    When I ask how long a 120 second cycle's first wait is, the random number being 0.25
    Then the result is "30"

  Scenario: The thread's first wait is within a cycle
    When I ask how long a 120 second cycle's first wait is
    Then the result is at least 0 and less than 120
