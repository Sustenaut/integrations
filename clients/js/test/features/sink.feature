@sink
Feature: caller.js's StorageSink: as tests/steps/test_storage_sink.py holds storage_sink.py

  Scenario: An idle cycle still writes a report
    Then an idle cycle still writes a report

  Scenario: Each dataset is its own tenant, and a cycle's calls are one file
    Then each dataset is its own tenant, and a cycle's calls are one file

  Scenario: Past the limit the oldest go and are counted, as are a project's with no buffer
    Then past the limit the oldest go and are counted, as are a project's with no buffer

  Scenario: A refused file goes again as it was, under its name, and the failures are reported
    Then a refused file goes again as it was, under its name, and the failures are reported

  Scenario: No answer, a status without a reason, and a token that can't be read are failed writes too
    Then no answer, a status without a reason, and a token that can't be read are failed writes too

  Scenario: The token file is read for each request
    Then the token file is read for each request

  Scenario: Into an R2 bucket binding, under its prefix, for the storage Worker
    Then into an R2 bucket binding, under its prefix, for the storage Worker

  Scenario: A write that finds the file overdue sends it, and hands that to waitUntil
    Then a write that finds the file overdue sends it, and hands that to waitUntil

  Scenario: By default, a Worker's way: flush from write, with no timer
    Then by default, a Worker's way: flush from write, with no timer

  Scenario: In Node, start: true sends on a timer, first a random part of a cycle in, and close() stops it
    Then in Node, start: true sends on a timer, first a random part of a cycle in, and close() stops it

  Scenario: A sink needs a sender and somewhere to send
    Then a sink needs a sender and somewhere to send

  Scenario: Page times become the window's low, median and high, rounded as Python rounds
    Then page times become the window's low, median and high, rounded as Python rounds

  Scenario: Past the limit page times are still counted
    Then past the limit page times are still counted

  Scenario: The same records give the same bytes, and the name holds their hash
    Then the same records give the same bytes, and the name holds their hash

  Scenario: The file in tests/vectors/ is what the sink writes
    Then the file in tests/vectors/ is what the sink writes
