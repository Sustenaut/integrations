Feature: The scanner: files in storage reach BigQuery once and leave, whatever stops partway.

  Storage and BigQuery are fakes of the project's own. Each pass leaves, as its
  result, what they hold then: what BigQuery loaded, the folders of storage, and
  the scanner's reports, one a dataset a pass.

  Scenario: Waiting files load as one job and leave
    Given a scanner of the calls of "ds"
    And calls files 1, 2 and 3 are waiting
    When the scanner passes
    Then the result's "loaded" is "["r1", "r2", "r3"]"
    And the result's "posts" is "1"
    And the result's "left" is "[]"
    And the result's "reports.0.kind" is "ingest"
    And the result's "reports.0.dataset" is "ds"
    And the result's "reports.0.loaded_files" is "3"
    And the result's "reports.0.jobs_today" is "1"
    And the result's "reports.0.inbox_files" is "0"
    And the result's "reports.0.inbox_oldest_seconds" is "null"
    And the result's "reports.0.storage_ok" is "true"

  Scenario: An empty inbox spends no job and still reports
    Given a scanner of the calls of "ds"
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "report count" is "1"
    And the result's "reports.0.inbox_files" is "0"
    And the result's "reported" is "["ds"]"

  Scenario: A file with no records just leaves
    Given a scanner of the calls of "ds"
    And storage holds "inbox/calls/2027-01-15/empty.avro", with no records
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "left" is "[]"

  Scenario: A crash after the load leaves the files and the batch record
    Given a scanner of the calls of "ds"
    And calls files 1 and 2 are waiting
    And storage does not answer a "DELETE"
    When the scanner passes
    Then the result's "loaded" is "["r1", "r2"]"
    And the result's "inbox" is "["inbox/calls/2027-01-15/075001-web-1.avro", "inbox/calls/2027-01-15/075002-web-2.avro"]"
    And the result's "batch count" is "1"

  Scenario: A crash after the load doesnt load again
    Given a scanner of the calls of "ds"
    And calls files 1 and 2 are waiting
    And storage does not answer a "DELETE"
    When the scanner passes
    And storage answers again
    And the scanner passes again
    Then the result's "loaded" is "["r1", "r2"]"
    And the result's "left" is "[]"

  Scenario: A crash partway through deleting finishes with what is left
    Given a scanner of the calls of "ds"
    And calls files 1 and 2 are waiting
    And storage does not answer a "DELETE"
    When the scanner passes
    And storage answers again
    And "inbox/calls/2027-01-15/075001-web-1.avro" is gone from storage
    And the scanner passes again
    Then the result's "loaded" is "["r1", "r2"]"
    And the result's "left" is "[]"

  Scenario: A crash while splitting a rejected batch loads nothing twice
    Given a scanner of the calls of "ds"
    And calls files 1, 2 and 3 are waiting
    And calls file 4 is waiting with a bad record
    And storage does not answer a "DELETE"
    When the scanner passes
    And storage answers again
    And the scanner passes again
    Then the result's "loaded" is "["r1", "r2", "r3"]"
    And the result's "inbox" is "[]"
    And the result's "batches" is "[]"
    And the result's "rejected" is "["rejected/calls/2027-01-15/075004-web-4.avro"]"

  Scenario: A batch whose files have all gone only loses its record
    Given a scanner of the calls of "ds"
    And storage holds the batch record "batches/old.json":
      """
      {"files": ["inbox/calls/gone.avro"], "attempt": 0}
      """
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "left" is "[]"

  Scenario: An unfinished batch keeps its record
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting
    And BigQuery leaves its load jobs running
    When the scanner passes
    Then the result's "batch count" is "1"

  Scenario: New files wait while an unfinished batch does
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting
    And BigQuery leaves its load jobs running
    When the scanner passes
    And calls file 2 arrives
    And the scanner passes again
    Then the result's "calls jobs" is "0"
    And the result's "inbox" is "["inbox/calls/2027-01-15/075001-web-1.avro", "inbox/calls/2027-01-15/075002-web-2.avro"]"
    And the result's "reports.-1.problems" includes "did not finish"
    And the result's "reports.-1.inbox_files" is "3"

  Scenario: A job that failed in a way that may pass waits for its next try
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting
    And BigQuery's next calls job fails in a way that may pass
    When the scanner passes
    Then the result's "loaded" is "[]"
    And the result's "inbox" is "["inbox/calls/2027-01-15/075001-web-1.avro"]"
    And the result's "batch.attempt" is "1"
    And the result's "reports.0.problems" includes "backendError"

  Scenario: A job that failed in a way that may pass goes again as a new job
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting
    And BigQuery's next calls job fails in a way that may pass
    When the scanner passes
    And the scanner passes again
    Then the result's "loaded" is "["r1"]"
    And the result's "left" is "[]"
    And the result's "calls jobs" is "2"

  Scenario: Bad files alone move to rejected
    Given a scanner of the calls of "ds"
    And calls files 1, 2 and 4 are waiting
    And calls file 3 is waiting with a bad record
    When the scanner passes
    Then the result's "loaded" is "["r1", "r2", "r4"]"
    And the result's "inbox" is "[]"
    And the result's "rejected" is "["rejected/calls/2027-01-15/075003-web-3.avro"]"
    And the result's "rejected as they came" is "["rejected/calls/2027-01-15/075003-web-3.avro"]"
    And the result's "batches" is "[]"
    And the result's "reports.0.rejected_now" is "1"
    And the result's "reports.0.rejected_files" is "1"
    And the result's "reports.0.rejected_oldest_seconds" is "600.0"
    And the result's "reports.0.problems" includes "invalid"

  Scenario: Finding a bad file takes few jobs
    Given a scanner of the calls of "ds"
    And calls files 1 to 16 are waiting
    And calls file 17 is waiting with a bad record
    When the scanner passes
    Then the result's "loaded" is "["r1", "r10", "r11", "r12", "r13", "r14", "r15", "r16", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9"]"
    And the result's "posts" is "11"

  Scenario: A file that isnt avro is rejected without a job
    Given a scanner of the calls of "ds"
    And storage holds "inbox/calls/2027-01-15/broken.avro", with no Avro
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "rejected" is "["rejected/calls/2027-01-15/broken.avro"]"
    And the result's "reports.0.problems" includes "can't be read"

  Scenario: Rows that cant be call records are rejected
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting
    And call records can't be encoded
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "rejected" is "["rejected/calls/2027-01-15/075001-web-1.avro"]"
    And the result's "reports.0.problems" includes "not call records"

  Scenario: The days job limit keeps files waiting
    Given a scanner of the calls of "ds"
    And the day's load jobs of the calls are used
    And calls file 1 is waiting
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "inbox" is "["inbox/calls/2027-01-15/075001-web-1.avro"]"
    And the result's "reports.0.jobs_today" is "1400"
    And the result's "reports.0.problems" includes "load jobs"

  Scenario: A batch is kept to as many files as fit in memory
    When the scanner chooses a batch from 505 files of 1 byte
    Then the result has 500 files

  Scenario: A batch is kept to as many bytes as fit in memory
    When the scanner chooses a batch from:
      | name | size     |
      | a    | 32000000 |
      | b    | 1        |
    Then the result is "["a"]"

  Scenario: A file too big for memory is still a batch of its own
    When the scanner chooses a batch from:
      | name | size     |
      | huge | 32000001 |
    Then the result is "["huge"]"

  Scenario: Storage down leaves everything and says so
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting
    And storage does not answer a "GET"
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "inbox" is "["inbox/calls/2027-01-15/075001-web-1.avro"]"
    And the result's "reports.0.storage_ok" is "false"
    And the result's "reports.0.problems" includes "no answer"

  Scenario: A batch record that cant be written means no load
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting
    And storage does not answer a "PUT"
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "reports.0.storage_ok" is "false"
    And the result's "reports.0.problems" includes "no answer"

  Scenario: A refusal says what was refused
    Given the "storage.example" service answers "PUT /ds/inbox/calls/a.avro" with 403 and:
      """
      {}
      """
    When the inbox writes "inbox/calls/a.avro" for "ds"
    Then it is refused, saying "write: HTTP 403"
    And the refusal is a "StorageDown"
    And the "storage.example" service was asked "PUT /ds/inbox/calls/a.avro" with the header "Authorization" set to "Bearer pod-token"

  Scenario: One projects bad day doesnt stop the next
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting
    And the work on "broken" fails with "boom"
    When the scanner passes over "broken, ds"
    Then the result's "reports.0.dataset" is "broken"
    And the result's "reports.0.problems" includes "RuntimeError: boom"
    And the result's "loaded" is "["r1"]"
    And the result's "reports.1.dataset" is "ds"

  Scenario: Backlog is reported with its age
    Given a scanner of the calls of "ds"
    And calls files 1 and 2 are waiting
    And BigQuery is busy
    When the scanner passes
    Then the result's "reports.0.inbox_files" is "2"
    And the result's "reports.0.inbox_bytes" is "1616"
    And the result's "reports.0.inbox_more" is "false"
    And the result's "reports.0.inbox_oldest_seconds" is "600"

  Scenario: A senders report reaches the status table and not calls
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report of 1 call and 3 drops
    When the scanner passes
    Then the result's "loaded" is "["r1"]"
    And the result's "left" is "[]"
    And the result's "status kinds" is "["sender"]"
    And the result's "status rows.0.sender" is "web-1"
    And the result's "status rows.0.calls" is "1"
    And the result's "status rows.0.drops" is "3"
    And the result's "jobs used" is "1"
    And the result's "status jobs used" is "1"

  Scenario: An idle senders file has a report and spends no calls job
    Given a scanner of the calls of "ds"
    And storage holds "inbox/calls/2027-01-15/075000-web-idle.avro", with no records and an idle sender's report
    When the scanner passes
    Then the result's "posts" is "0"
    And the result's "left" is "[]"
    And the result's "status kinds" is "["sender"]"
    And the result's "status rows.0.calls" is "0"

  Scenario: Many reports are one job
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report from "web-1"
    And calls file 2 is waiting with a report from "web-2"
    And calls file 3 is waiting with a report from "web-3"
    When the scanner passes
    Then the result's "status jobs" is "1"
    And the result's "senders" is "["web-1", "web-2", "web-3"]"
    And the result's "status kinds" is "["sender", "sender", "sender"]"

  Scenario: The pod writes its report of each pass into the inbox
    Given a scanner of the calls of "ds"
    When the scanner passes
    Then the result's "own report count" is "1"
    And the result's "own report.kind" is "ingest"
    And the result's "own report.inbox_files" is "0"
    # It is the pod's, whatever it scanned: it names no dataset.
    And the result's "own report fields" is "["at", "cycle_end", "cycle_start", "inbox_bytes", "inbox_files", "inbox_more", "inbox_oldest_seconds", "jobs_today", "kind", "loaded_files", "problems", "rejected_files", "rejected_more", "rejected_now", "rejected_oldest_seconds", "sender", "status_jobs_today", "storage_ok", "version"]"

  Scenario: The pod's report loads on the next pass
    Given a scanner of the calls of "ds"
    When the scanner passes
    And the scanner passes again
    Then the result's "status kinds" is "["ingest"]"
    And the result's "own report count" is "1"
    And the result's "posts" is "0"
    And the result's "earlier own reports kept" is "[]"

  Scenario: The pods report says what went wrong
    Given a scanner of the calls of "ds"
    And storage holds "inbox/calls/2027-01-15/broken.avro", with no Avro
    When the scanner passes
    Then the result's "own report.problems" includes "can't be read"
    And the result's "reports.0.problems" includes "can't be read"

  Scenario: Reports load though the calls job fails
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report from "web-1"
    And BigQuery's next calls job fails in a way that may pass
    When the scanner passes
    Then the result's "status kinds" is "["sender"]"
    And the result's "loaded" is "[]"

  Scenario: Reports arent loaded twice when the calls go again
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report from "web-1"
    And BigQuery's next calls job fails in a way that may pass
    When the scanner passes
    And the scanner passes again
    Then the result's "loaded" is "["r1"]"
    And the result's "senders" is "["web-1"]"

  Scenario: A crash after the reports loaded doesnt load them again
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report from "web-1"
    And storage does not answer a "DELETE"
    When the scanner passes
    And storage answers again
    And the scanner passes again
    Then the result's "senders" is "["web-1"]"
    And the result's "loaded" is "["r1"]"
    And the result's "left" is "[]"

  Scenario: A report job that failed holds up the calls
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report from "web-1"
    And BigQuery's next report job fails in a way that may pass
    When the scanner passes
    Then the result's "loaded" is "[]"
    And the result's "status rows" is "[]"
    And the result's "reports.0.problems" includes "backendError"

  Scenario: A report job that failed goes again before the calls
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report from "web-1"
    And BigQuery's next report job fails in a way that may pass
    When the scanner passes
    And the scanner passes again
    Then the result's "loaded" is "["r1"]"
    And the result's "left" is "[]"
    And the result's "senders" is "["web-1"]"

  Scenario: Rejected reports dont hold up the calls
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report from "web-1"
    And BigQuery rejects the reports
    When the scanner passes
    Then the result's "loaded" is "["r1"]"
    And the result's "left" is "[]"
    And the result's "reports.0.problems" includes "reports rejected"

  Scenario: A report that isnt one only loses the report
    Given a scanner of the calls of "ds"
    And calls file 1 is waiting with a report that is not one
    When the scanner passes
    Then the result's "loaded" is "["r1"]"
    And the result's "status rows" is "[]"
    And the result's "reports.0.problems" includes "bad report"

  Scenario: The status table's day of jobs leaves the calls' own
    Given a scanner of the calls of "ds"
    And the day's load jobs of the status table are used
    When I ask whether the calls may load
    Then the result is "true"

  Scenario: The status table has its own days jobs
    Given a scanner of the calls of "ds"
    And the day's load jobs of the status table are used
    And calls file 1 is waiting with a report from "web-1"
    When the scanner passes
    Then the result's "loaded" is "[]"
    And the result's "inbox" is "["inbox/calls/2027-01-15/075001-web-1.avro"]"
    And the result's "reports.0.problems" includes "load jobs"
    And the result's "reports.0.status_jobs_today" is "1400"

  Scenario: Signals load into their own table from their own tenant and leave
    Given a scanner of the signals of "observability", in the tenant "signals"
    And storage holds "inbox/signals/2027-01-15/075001-evaluator-1.avro", with signal 1
    And storage holds "inbox/signals/2027-01-15/075002-evaluator-2.avro", with signal 2
    When the scanner passes
    Then the result's "tables" is "["signals"]"
    And the result's "signals" is "["example/app1", "example/app2"]"
    And the result's "signal evidence.0" is "{"dropped": 3}"
    # Its report goes to the log only: the tenant keeps nothing but signals.
    And the result's "files" is "[]"
    And the result's "reports.0.dataset" is "observability"
    And the result's "reports.0.loaded_files" is "2"
    And the result's "reports.0.problems" is "null"
    And the result's "storage folders asked" is "["batches", "inbox", "list"]"

  Scenario: A file in the signals inbox that isnt signals is rejected
    Given a scanner of the signals of "observability", in the tenant "signals"
    And storage holds "inbox/signals/2027-01-15/075000-evaluator-x.avro", with call record 1
    When the scanner passes
    Then the result's "signals" is "[]"
    And the result's "rejected" is "["rejected/signals/2027-01-15/075000-evaluator-x.avro"]"
    And the result's "reports.0.problems" includes "not signals"

  Scenario Outline: Rows of each kind are the same bytes for the same rows
    When <rows> are written twice and read back
    Then the result's "the same bytes" is "true"
    And the result's "read back unchanged" is "true"

    Examples:
      | rows            |
      | signals         |
      | need states     |
      | pubrules checks |

  Scenario Outline: Ingest signals names the tenant and dataset
    When INGEST_SIGNALS is <text>
    Then the result's "tenants" is "<tenants>"
    And the result's "kinds" is "<kinds>"

    Examples:
      | text                                                 | tenants                       | kinds                     |
      | ""                                                   | {}                            | {}                        |
      | "  "                                                 | {}                            | {}                        |
      | "{"tenant": "signals", "dataset": "observability"}" | {"observability": "signals"} | {"observability": "signals"} |

  Scenario Outline: A wrong ingest signals is refused
    When INGEST_SIGNALS is "<text>"
    Then it is refused, saying "INGEST_SIGNALS is"
    And the refusal is a "ValueError"

    Examples:
      | text                                         |
      | ["signals"]                                  |
      | {"tenant": "signals"}                        |
      | {"tenant": "", "dataset": "observability"}   |

  Scenario: Need states load into their own table from their own tenant and leave
    Given a scanner of the need states of "convergence", in the tenant "need-states"
    And storage holds "inbox/need-states/2027-01-15/075000-example-org.app.app-1.avro", with need state 1 ready and need state 2 missing
    And storage holds "inbox/need-states/2027-01-15/075001-example-org.app.app-2.avro", with signal 1
    When the scanner passes
    Then the result's "tables" is "["need_states"]"
    And the result's "need states" is "[["k8s.secret:s1", "ready"], ["k8s.secret:s2", "missing"]]"
    # Read against the need-state schema, so a file of anything else is set aside alone.
    And the result's "rejected" is "["rejected/need-states/2027-01-15/075001-example-org.app.app-2.avro"]"
    And the result's "reports.0.problems" includes "can't be read"

  Scenario: Ingest need states names its tenant and dataset beside the signals
    When the ingest buffers are read from:
      | INGEST_SIGNALS     | {"tenant": "signals", "dataset": "observability"}   |
      | INGEST_NEED_STATES | {"tenant": "need-states", "dataset": "convergence"} |
    Then the result is:
      """
      {"tenants": {"observability": "signals", "convergence": "need-states"},
       "kinds": {"observability": "signals", "convergence": "need_states"}}
      """

  Scenario: No ingest buffers named is none
    When the ingest buffers are read from:
      | INGEST_SIGNALS     |  |
      | INGEST_NEED_STATES |  |
    Then the result is:
      """
      {"tenants": {}, "kinds": {}}
      """

  Scenario: Ingest need states with no dataset is refused
    When INGEST_NEED_STATES is "{"tenant": "need-states"}"
    Then it is refused, saying "INGEST_NEED_STATES is"
    And the refusal is a "ValueError"

  Scenario: Signals and need states may not name the same dataset
    When the ingest buffers are read from:
      | INGEST_SIGNALS     | {"tenant": "signals", "dataset": "x"}     |
      | INGEST_NEED_STATES | {"tenant": "need-states", "dataset": "x"} |
    Then it is refused, saying "name the same dataset"
    And the refusal is a "ValueError"

  Scenario: Pubrules checks load into their own table from their own tenant and leave
    Given a scanner of the pubrules checks of "platform", in the tenant "pubrules-checks"
    And storage holds "inbox/pubrules-checks/2027-01-15/024100-nightly-1.avro", with pubrules check 1 passing and pubrules check 2 failing
    And storage holds "inbox/pubrules-checks/2027-01-15/024101-nightly-2.avro", with signal 1
    When the scanner passes
    Then the result's "tables" is "["pubrules_checks"]"
    And the result's "pubrules checks" is "[["example-org/repo1", "pass"], ["example-org/repo2", "fail"]]"
    And the result's "rejected" is "["rejected/pubrules-checks/2027-01-15/024101-nightly-2.avro"]"
    And the result's "reports.0.problems" includes "can't be read"

  Scenario: Ingest pubrules checks names its tenant and dataset beside the others
    When the ingest buffers are read from:
      | INGEST_SIGNALS         | {"tenant": "signals", "dataset": "observability"}   |
      | INGEST_NEED_STATES     | {"tenant": "need-states", "dataset": "convergence"} |
      | INGEST_PUBRULES_CHECKS | {"tenant": "pubrules-checks", "dataset": "platform"} |
    Then the result's "tenants" is "{"observability": "signals", "convergence": "need-states", "platform": "pubrules-checks"}"
    And the result's "kinds.platform" is "pubrules_checks"

  Scenario: Ingest pubrules checks with no tenant is refused
    When INGEST_PUBRULES_CHECKS is "{"dataset": "platform"}"
    Then it is refused, saying "INGEST_PUBRULES_CHECKS is"
    And the refusal is a "ValueError"

  Scenario: Need states and pubrules checks may not name the same dataset
    When the ingest buffers are read from:
      | INGEST_NEED_STATES     | {"tenant": "need-states", "dataset": "x"}     |
      | INGEST_PUBRULES_CHECKS | {"tenant": "pubrules-checks", "dataset": "x"} |
    Then it is refused, saying "INGEST_NEED_STATES and INGEST_PUBRULES_CHECKS name the same dataset"
    And the refusal is a "ValueError"

  Scenario: The scan passes once at start and then each interval
    When the scan runs over "a,b" until its second sleep, at the moment 42
    Then the result's "events" is "["tick a,b", "sleep 120", "tick a,b", "sleep 120"]"
    And the result's "ticked" is "[42.0]"

  Scenario: CI jobs load from the tenant they share, and leave the cluster usage waiting
    Given a scanner of the buffers INGEST_PUBRULES_CHECKS and INGEST_BUFFERS name:
      | INGEST_PUBRULES_CHECKS | {"tenant": "pubrules-checks", "dataset": "platform"} |
      | INGEST_BUFFERS         | [{"tenant": "ci-usage", "dataset": "platform", "table": "ci_jobs"}, {"tenant": "ci-usage", "dataset": "platform", "table": "cluster_usage"}] |
    And storage holds "inbox/ci-jobs/2027-01-15/024100-ci-jobs-1.avro", with a CI job that waited 5 minutes
    And storage holds "inbox/cluster-usage/2027-01-15/024000-usage-1.avro", with a node using 400 millicores
    # A batch of the other Kind's, left by a pod stopped partway, is not this one's to resume.
    And storage holds the batch record "batches/cluster_usage-left.json":
      """
      {"files": ["inbox/cluster-usage/2027-01-15/024000-usage-1.avro"], "attempt": 0, "status_attempt": 0, "status_done": false}
      """
    When the scanner passes over "platform.ci_jobs"
    Then the result's "tables" is "["ci_jobs"]"
    And the result's "datasets" is "["platform"]"
    And the result's "CI job waits" is "[300]"
    And the result's "inbox" is "["inbox/cluster-usage/2027-01-15/024000-usage-1.avro"]"
    And the result's "reports.0.dataset" is "platform"
    And the result's "reports.0.table" is "ci_jobs"

  Scenario: CI jobs and cluster usage share a tenant and load into their own tables
    Given a scanner of the buffers INGEST_PUBRULES_CHECKS and INGEST_BUFFERS name:
      | INGEST_PUBRULES_CHECKS | {"tenant": "pubrules-checks", "dataset": "platform"} |
      | INGEST_BUFFERS         | [{"tenant": "ci-usage", "dataset": "platform", "table": "ci_jobs"}, {"tenant": "ci-usage", "dataset": "platform", "table": "cluster_usage"}] |
    And storage holds "inbox/ci-jobs/2027-01-15/024100-ci-jobs-1.avro", with a CI job that waited 5 minutes
    And storage holds "inbox/cluster-usage/2027-01-15/024000-usage-1.avro", with a node using 400 millicores
    And storage holds the batch record "batches/cluster_usage-left.json":
      """
      {"files": ["inbox/cluster-usage/2027-01-15/024000-usage-1.avro"], "attempt": 0, "status_attempt": 0, "status_done": false}
      """
    When the scanner passes over "platform.ci_jobs"
    And the scanner passes over "platform.cluster_usage, platform"
    Then the result's "tables" is "["ci_jobs", "cluster_usage"]"
    And the result's "cluster millicores" is "[400]"
    And the result's "inbox" is "[]"
    And the result's "batches" is "[]"
    And the result's "reported" is "["platform.ci_jobs", "platform.cluster_usage", "platform.pubrules_checks"]"

  Scenario: INGEST_BUFFERS names more buffers beside the three single variables
    When the ingest buffers are read from:
      | INGEST_SIGNALS         | {"tenant": "signals", "dataset": "observability"}   |
      | INGEST_NEED_STATES     | {"tenant": "need-states", "dataset": "convergence"} |
      | INGEST_PUBRULES_CHECKS | {"tenant": "pubrules-checks", "dataset": "platform"} |
      | INGEST_BUFFERS         | [{"tenant": "ci-usage", "dataset": "platform", "table": "ci_jobs"}, {"tenant": "ci-usage", "dataset": "platform", "table": "cluster_usage"}] |
    Then the result is:
      """
      {"tenants": {"observability": "signals", "convergence": "need-states", "platform": "pubrules-checks",
                   "platform.ci_jobs": "ci-usage", "platform.cluster_usage": "ci-usage"},
       "kinds": {"observability": "signals", "convergence": "need_states", "platform": "pubrules_checks",
                 "platform.ci_jobs": "ci_jobs", "platform.cluster_usage": "cluster_usage"}}
      """

  Scenario: An empty INGEST_BUFFERS names nothing
    When INGEST_BUFFERS is ""
    Then the result is:
      """
      {"tenants": {}, "kinds": {}}
      """

  Scenario Outline: A wrong INGEST_BUFFERS is refused
    When INGEST_BUFFERS is "<text>"
    Then it is refused, saying "<says>"
    And the refusal is a "ValueError"

    Examples:
      | text                                                     | says               |
      | {"tenant": "x"}                                          | INGEST_BUFFERS is  |
      | [{"tenant": "x", "dataset": "d"}]                        | INGEST_BUFFERS is  |
      | [{"tenant": "x", "dataset": "d", "table": "calls"}]      | names table calls  |
      | [{"tenant": "x", "dataset": "a.b", "table": "ci_jobs"}]  | can't hold a dot   |

  Scenario: INGEST_BUFFERS may not name a buffer twice
    When INGEST_BUFFERS names the ci_jobs buffer of platform twice
    Then it is refused, saying "names platform.ci_jobs twice"
    And the refusal is a "ValueError"

  Scenario: INGEST_BUFFERS may not load a table a single variable loads
    When the ingest buffers are read from:
      | INGEST_PUBRULES_CHECKS | {"tenant": "pubrules-checks", "dataset": "platform"} |
      | INGEST_BUFFERS         | [{"tenant": "p", "dataset": "platform", "table": "pubrules_checks"}] |
    Then it is refused, saying "INGEST_PUBRULES_CHECKS and INGEST_BUFFERS both load platform.pubrules_checks"
    And the refusal is a "ValueError"
