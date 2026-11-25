@sink
Feature: caller.js's StorageSink, as tests/steps/test_storage_sink.py holds storage_sink.py
  The sink's storage keeps each file it is sent in the scratch directory, under the
  request's path; its clock starts at 2027-01-15T08:00:00Z, it sends only when it is
  flushed, it keeps 3 records a dataset, and its token is "pod-token", unless a Given
  says otherwise. Records are named by request id: "r1,r2" is two call records of a-repo.

  Background:
    Given a scratch directory

  Scenario: An idle cycle still writes a report
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    And storage says on stdout what it is sent
    When two minutes pass
    And it flushes
    Then the directory "ds/inbox/calls/2027-01-15" holds:
      """
      080200-app-1-x-465bdc5d480c230e.avro
      """
    And the report in "ds/inbox/calls/2027-01-15/080200-app-1-x-465bdc5d480c230e.avro" is:
      """
      {"kind": "sender", "sender": "app-1_x", "version": "js", "at": "2027-01-15T08:02:00Z",
       "cycle_start": "2027-01-15T08:00:00Z", "cycle_end": "2027-01-15T08:02:00Z", "calls": 0, "drops": 0,
       "buffer_size": 0, "storage_failures": 0, "page_count": 0, "page_ms_low": null, "page_ms_median": null,
       "page_ms_high": null, "cold_start": true}
      """
    And the Avro metadata of "ds/inbox/calls/2027-01-15/080200-app-1-x-465bdc5d480c230e.avro" has "avro.codec" equal to "null"
    And it prints the JSON:
      """
      {"url": "https://storage.example/ds/inbox/calls/2027-01-15/080200-app-1-x-465bdc5d480c230e.avro", "method": "PUT",
       "headers": {"Authorization": "Bearer pod-token", "User-Agent": "integrations-caller.js"}, "body": null,
       "signal": "live"}
      """

  Scenario: Each dataset is its own tenant, and a cycle's calls are one file
    Given a StorageSink "s" for:
      """
      {"a-repo": "proj_a", "b-repo": "proj_b"}
      """
    And its limit is the default
    And its token is a function giving "pod-token"
    When it is given the records "r1,r2"
    And it is given the records "r3" for "b-repo"
    And it flushes
    Then the directory "." holds:
      """
      proj-a/
      proj-b/
      """
    And the report in "proj-a/inbox/calls/2027-01-15/080000-s-af991206b54fc8eb.avro" has "calls" equal to "2"
    And the report in "proj-b/inbox/calls/2027-01-15/080000-s-1277873f13089ed4.avro" has "calls" equal to "1"

  Scenario: Past the limit the oldest go, as do a project's with no buffer
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    When it is given the records "r1,r2,r3,r4,r5"
    And it is given the records "r6" for "elsewhere"
    Then the result is:
      """
      {"ds": ["r3", "r4", "r5"]}
      """

  Scenario: What went is counted in the next report
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    When it is given the records "r1,r2,r3,r4,r5"
    And it is given the records "r6" for "elsewhere"
    And it flushes
    Then the report in "ds/inbox/calls/2027-01-15/080000-app-1-x-fd0cbad69b672fbd.avro" has "calls" equal to "3"
    And the report in "ds/inbox/calls/2027-01-15/080000-app-1-x-fd0cbad69b672fbd.avro" has "drops" equal to "3"
    And the report in "ds/inbox/calls/2027-01-15/080000-app-1-x-fd0cbad69b672fbd.avro" has "buffer_size" equal to "3"

  Scenario: A sink with no datasets takes a record and flushes without failing
    Given a StorageSink "s" into an R2 bucket with the prefix "", for:
      """
      {}
      """
    When it is given the records "r1"
    And it flushes
    Then it is not refused
    And the file "inbox" does not exist

  Scenario: A refused file is held, and goes again as it was, under its name, before anything new
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    And storage refuses with 403 and:
      """
      {"error": "github:x may not write here"}
      """
    When it is given the records "r1"
    And two minutes pass
    And it flushes
    And it is given the records "r2"
    And two minutes pass
    And storage says on stdout what it is sent
    And it flushes
    Then the file "ds" does not exist
    And it prints the JSON:
      """
      {"url": "https://storage.example/ds/inbox/calls/2027-01-15/080200-app-1-x-c89810b2b054c054.avro", "method": "PUT",
       "headers": {"Authorization": "Bearer pod-token", "User-Agent": "integrations-caller.js"}, "body": null,
       "signal": "live"}
      """
    And stderr mentions "integrations: storage didn't take inbox/calls/2027-01-15/080200-app-1-x-c89810b2b054c054.avro for ds: HTTP 403 github:x may not write here"

  Scenario: Once storage takes it, the held file goes first, and the next reports the failures
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    And storage refuses with 403 and:
      """
      {"error": "github:x may not write here"}
      """
    When it is given the records "r1"
    And two minutes pass
    And it flushes
    And it is given the records "r2"
    And two minutes pass
    And it flushes
    And storage takes files again
    And two minutes pass
    And storage says on stdout what it is sent
    And it flushes
    Then it prints:
      """
      {"url":"https://storage.example/ds/inbox/calls/2027-01-15/080200-app-1-x-c89810b2b054c054.avro","method":"PUT","headers":{"Authorization":"Bearer pod-token","User-Agent":"integrations-caller.js"},"body":null,"signal":"live"}
      {"url":"https://storage.example/ds/inbox/calls/2027-01-15/080600-app-1-x-5d05f88049d2efb2.avro","method":"PUT","headers":{"Authorization":"Bearer pod-token","User-Agent":"integrations-caller.js"},"body":null,"signal":"live"}
      """
    And the directory "ds/inbox/calls/2027-01-15" holds:
      """
      080200-app-1-x-c89810b2b054c054.avro
      080600-app-1-x-5d05f88049d2efb2.avro
      """
    And the report in "ds/inbox/calls/2027-01-15/080200-app-1-x-c89810b2b054c054.avro" has "calls" equal to "1"
    And the report in "ds/inbox/calls/2027-01-15/080600-app-1-x-5d05f88049d2efb2.avro" has "calls" equal to "1"
    And the report in "ds/inbox/calls/2027-01-15/080600-app-1-x-5d05f88049d2efb2.avro" has "storage_failures" equal to "2"

  Scenario: No answer is a failed write
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    And storage is down
    When it flushes
    Then stderr has a line matching "integrations: storage didn't take *: no answer: fetch failed"

  Scenario: A status without a reason is a failed write
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    And storage refuses with 500 and:
      """
      <html>
      """
    When it flushes
    Then stderr has a line matching "integrations: storage didn't take *: HTTP 500"

  Scenario: A token that can't be read is a failed write, which says why
    Given a StorageSink "s" for:
      """
      {"a-repo": "ds"}
      """
    And its token is read from "/no/such/token", which isn't there
    And storage refuses with 401 and:
      """
      {}
      """
    And storage says on stdout what it is sent
    When it flushes
    Then the JSON it prints has "headers.Authorization" equal to "Bearer "
    And stderr mentions "HTTP 401; no token to send: ENOENT"

  Scenario: The token file is read for each request
    Given a StorageSink "s" for:
      """
      {"a-repo": "ds"}
      """
    And its token is read from "/var/run/token", which holds "first", then "second"
    And storage says on stdout what it is sent
    When it flushes 2 times
    Then it prints:
      """
      read /var/run/token utf8
      {"url":"https://storage.example/ds/inbox/calls/2027-01-15/080000-s-7ce32abc7911b4d0.avro","method":"PUT","headers":{"Authorization":"Bearer first","User-Agent":"integrations-caller.js"},"body":null,"signal":"live"}
      read /var/run/token utf8
      {"url":"https://storage.example/ds/inbox/calls/2027-01-15/080000-s-eeda003213b0f215.avro","method":"PUT","headers":{"Authorization":"Bearer second","User-Agent":"integrations-caller.js"},"body":null,"signal":"live"}
      """

  Scenario: Into an R2 bucket binding, for the storage Worker, a file R2 didn't take going again
    Given a StorageSink "storage" into an R2 bucket with the prefix "", for:
      """
      {"a-repo": "proj_a"}
      """
    And R2 is down
    When it is given the records "r1"
    And it flushes
    And R2 comes back
    And it flushes
    Then the directory "proj-a/inbox/calls/2027-01-15" holds:
      """
      080000-storage-ddf712e91cabd8c8.avro
      080000-storage-defdd8798b3d6da7.avro
      """
    And the report in "proj-a/inbox/calls/2027-01-15/080000-storage-ddf712e91cabd8c8.avro" has "storage_failures" equal to "1"

  Scenario: Into an R2 bucket, under its prefix
    Given a StorageSink "s" into an R2 bucket with the prefix "x/", for:
      """
      {"a-repo": "ds"}
      """
    When it flushes
    Then the directory "x/ds/inbox/calls/2027-01-15" holds:
      """
      080000-s-7ce32abc7911b4d0.avro
      """

  Scenario: A write that finds the file not yet due sends nothing
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    And it flushes from a write that finds the file overdue, handing that to waitUntil
    When it is given the records "r1"
    Then stdout is empty
    And the file "ds" does not exist

  Scenario: A write that finds the file overdue sends it, and hands that to waitUntil, once
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    And it flushes from a write that finds the file overdue, handing that to waitUntil
    When it is given the records "r1"
    And two minutes pass
    And it is given the records "r2,r3"
    Then it prints:
      """
      handed a flush to waitUntil
      """
    And the result is:
      """
      {"ds": ["r3"]}
      """
    And the directory "ds/inbox/calls/2027-01-15" holds:
      """
      080200-app-1-x-7d166652a769b5eb.avro
      """
    And the report in "ds/inbox/calls/2027-01-15/080200-app-1-x-7d166652a769b5eb.avro" has "calls" equal to "2"

  Scenario: By default, a sink starts no timer
    Given a StorageSink "s" for:
      """
      {"a-repo": "ds"}
      """
    And it flushes as it does by default
    When it is made
    Then the result's "timer" is "null"

  Scenario: By default, a Worker's way: flush from write
    Given a StorageSink "s" for:
      """
      {"a-repo": "ds"}
      """
    And it flushes as it does by default
    When it is made
    And two minutes pass
    And it is given the records "r1"
    Then the directory "ds/inbox/calls/2027-01-15" holds:
      """
      080200-s-55e33b02c9d40732.avro
      """

  Scenario: In Node, start: true sends on a timer, first a random part of a cycle in, and close() stops it
    Given a StorageSink "s" for:
      """
      {"a-repo": "ds"}
      """
    When it is started with a random part of 0.25 of a 120 s cycle, and the timers run "29999,1,120000" ms, then it is closed and they run 240000 ms more
    Then the result is:
      """
      [0, 1, 2, 3, 3]
      """

  Scenario Outline: A sink needs a sender and somewhere to send
    When I make a "StorageSink" from "caller.js" of:
      """
      [{}, <options>]
      """
    Then it is refused, saying "<says>"

    Examples:
      | options                              | says                    |
      | {"url": "https://s", "token": "t"}   | needs a sender          |
      | {"sender": "s", "url": "https://s"}  | needs a url and a token |

  Scenario: Page times become the window's low, median and high, rounded as Python rounds
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    When it times pages of "3,2,9,1" ms
    And two minutes pass
    And it flushes
    And it times pages of "0.5,1.5,2.5" ms
    And two minutes pass
    And it flushes
    And two minutes pass
    And it flushes
    Then the report in "ds/inbox/calls/2027-01-15/080200-app-1-x-fae18919c39726ac.avro" has "page_count" equal to "4"
    And the report in "ds/inbox/calls/2027-01-15/080200-app-1-x-fae18919c39726ac.avro" has "page_ms_low" equal to "1"
    And the report in "ds/inbox/calls/2027-01-15/080200-app-1-x-fae18919c39726ac.avro" has "page_ms_median" equal to "2"
    And the report in "ds/inbox/calls/2027-01-15/080200-app-1-x-fae18919c39726ac.avro" has "page_ms_high" equal to "9"
    And the report in "ds/inbox/calls/2027-01-15/080200-app-1-x-fae18919c39726ac.avro" has "cold_start" equal to "true"
    And the report in "ds/inbox/calls/2027-01-15/080400-app-1-x-123226dbc55ee1aa.avro" has "page_ms_low" equal to "0"
    And the report in "ds/inbox/calls/2027-01-15/080400-app-1-x-123226dbc55ee1aa.avro" has "page_ms_median" equal to "2"
    And the report in "ds/inbox/calls/2027-01-15/080400-app-1-x-123226dbc55ee1aa.avro" has "page_ms_high" equal to "2"
    And the report in "ds/inbox/calls/2027-01-15/080400-app-1-x-123226dbc55ee1aa.avro" has "cold_start" equal to "false"
    And the report in "ds/inbox/calls/2027-01-15/080600-app-1-x-5b0eb29d2571867f.avro" has "page_count" equal to "0"
    And the report in "ds/inbox/calls/2027-01-15/080600-app-1-x-5b0eb29d2571867f.avro" has "page_ms_median" equal to "null"

  Scenario: Past the limit page times are not kept
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    When it times 10005 pages of 1 ms
    Then the result has 10000 items

  Scenario: Past the limit page times are still counted
    Given a StorageSink "app-1_x" for:
      """
      {"a-repo": "ds", "b-repo": "ds"}
      """
    When it times 10005 pages of 1 ms
    And it flushes
    Then the report in "ds/inbox/calls/2027-01-15/080000-app-1-x-40d634bf5f844d23.avro" has "page_count" equal to "10005"

  Scenario Outline: The same records give the same bytes, and the name holds their hash
    When I name a file for "a b/c" at the start, of the records "<records>" with a sender's report
    Then the result is "inbox/calls/2027-01-15/080000-a-b-c-<hash>.avro"

    Examples:
      | records | hash |
      | r1,r2   | 01002281e48b3503 |
      | r1      | dfb17eea7b32c25e |

  Scenario: A file with no report has none in its metadata
    When I encode no records, with no report, into "empty.avro"
    Then the Avro metadata of "empty.avro" has "integrations.status" equal to "null"
    And the Avro metadata of "empty.avro" has "integrations.schema" equal to "call-record"

  Scenario: The file in tests/vectors/ is what the sink writes
    When the sink of the vector writes its records and page times, two minutes in
    Then the result is the JSON in "tests/vectors/sink.json"
    And the file "ds/inbox/calls/2027-01-15/080200-app-1-x-251562cc672d4443.avro" is the repository's "tests/vectors/sink.avro"

  Scenario: With a deadline, a call goes once it has waited the deadline
    Given a StorageSink "app-1" for:
      """
      {"a-repo": "ds"}
      """
    And its deadline is 30 seconds and its heartbeat 28800
    When it is given the records "r1"
    And 29 seconds pass
    And it sends what is due
    Then the result is "[]"
    When 1 seconds pass
    And it sends what is due
    Then the result is:
      """
      ["ds"]
      """

  Scenario: With a deadline, a quiet sender writes nothing until its heartbeat
    Given a StorageSink "app-1" for:
      """
      {"a-repo": "ds"}
      """
    And its deadline is 30 seconds and its heartbeat 600
    When 599 seconds pass
    And it sends what is due
    Then the result is "[]"
    When 1 seconds pass
    And it sends what is due
    Then the result is:
      """
      ["ds"]
      """

  Scenario: The first heartbeat comes a random part of one in
    Given a StorageSink "app-1" for:
      """
      {"a-repo": "ds"}
      """
    And its heartbeat is 400 seconds, the first 0.25 of the way in
    When 100 seconds pass
    And it sends what is due
    Then the result is:
      """
      ["ds"]
      """

  Scenario: With a deadline, only the dataset with calls writes, and a held file goes alone
    Given a StorageSink "app-1" for:
      """
      {"a-repo": "one", "b-repo": "two"}
      """
    And its deadline is 30 seconds and its heartbeat 28800
    And storage is down
    When it is given the records "r1"
    And 30 seconds pass
    And it sends what is due
    And storage takes files again
    And 29 seconds pass
    And it sends what is due
    Then the result is "[]"
    When 1 seconds pass
    And it sends what is due
    Then the result is:
      """
      ["one"]
      """

  Scenario: With a deadline, a write that finds a call overdue sends it
    Given a StorageSink "run-1" for:
      """
      {"a-repo": "ds"}
      """
    And its deadline is 30 seconds and its heartbeat 28800
    And it flushes from a write that finds the file overdue, handing that to waitUntil
    When it is given the records "r1"
    And 30 seconds pass
    And it is given the records "r2"
    Then the result's "ds" is "[]"

  Scenario: The deadline and the heartbeat come from the environment
    Given the environment sets "CALL_LOG_DEADLINE_SECONDS" to "30"
    And the environment sets "CALL_LOG_HEARTBEAT_SECONDS" to "28800"
    And a StorageSink "app-1" for:
      """
      {"a-repo": "ds"}
      """
    And its deadline and heartbeat are the environment's
    When it is given the records "r1"
    And 30 seconds pass
    And it sends what is due
    Then the result is:
      """
      ["ds"]
      """

  Scenario: A deadline that isn't a number of seconds above 0 is refused
    Given the environment sets "CALL_LOG_DEADLINE_SECONDS" to "soon"
    And a StorageSink "app-1" for:
      """
      {"a-repo": "ds"}
      """
    And its deadline and heartbeat are the environment's
    When it is made
    Then it is refused, saying "CALL_LOG_DEADLINE_SECONDS is a number of seconds above 0"

  Scenario: With a deadline and no heartbeat, a quiet sender writes nothing, however long
    Given a StorageSink "app-1" for:
      """
      {"a-repo": "ds"}
      """
    And its deadline is 30 seconds and no heartbeat
    When 86400 seconds pass
    And it sends what is due
    Then the result is "[]"
    When it flushes
    Then the directory "." holds:
      """
      """

  Scenario: With a deadline and no heartbeat, calls still go at the deadline
    Given a StorageSink "app-1" for:
      """
      {"a-repo": "ds"}
      """
    And its deadline is 30 seconds and no heartbeat
    When it is given the records "r1"
    And 30 seconds pass
    And it sends what is due
    Then the result is:
      """
      ["ds"]
      """
