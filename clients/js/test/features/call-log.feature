@call-log
Feature: workers/call-log's Durable Object, on fakes of its storage and R2, and caller.js's CallLogSink that sends it records

  Scenario: Records are kept the moment they come, and the first sets the two-minute alarm
    Then records are kept the moment they come, and the first sets the two-minute alarm

  Scenario: The alarm writes one file of the cycle's records into the sender's buffer, and re-arms
    Then the alarm writes one file of the cycle's records into the sender's buffer, and re-arms

  Scenario: An idle cycle still writes a report, so a quiet sender can be told from a stopped one
    Then an idle cycle still writes a report, so a quiet sender can be told from a stopped one

  Scenario: A file R2 didn't take goes again as it was, under its name, and nothing is sent twice
    Then a file R2 didn't take goes again as it was, under its name, and nothing is sent twice

  Scenario: Past the limit the oldest records go and are counted
    Then past the limit the oldest records go and are counted

  Scenario: An instance is one sender's, and only a sender SENDERS names may use it
    Then an instance is one sender's, and only a sender SENDERS names may use it

  Scenario: An instance nobody has sent to does nothing on an alarm, and the Worker answers nothing itself
    Then an instance nobody has sent to does nothing on an alarm, and the Worker answers nothing itself

  Scenario: The report is where health checks and statistics can join, as StorageSink's is
    Then the report is where health checks and statistics can join, as StorageSink's is

  Scenario: A sink keeps a request's records and sends them to its sender's object in one call
    Then a sink keeps a request's records and sends them to its sender's object in one call

  Scenario: A sink that can't send says so and lets the records go, never failing the request
    Then a sink that can't send says so and lets the records go, never failing the request
