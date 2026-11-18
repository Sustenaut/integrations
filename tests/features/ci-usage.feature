Feature: CI's wait times and compute load
  The ci-jobs poller writes each finished GitHub Actions job of the board's repositories
  once, with how long it waited for a runner; the sampler writes the server's usage and
  each runner pod's peak memory. Both go to storage's ci-usage buffer.

  Storage here is the scratch directory: what a pass uploads is in uploads/, the head it
  moves to is state.json, the buffer's files are under buffer/, and the rows they hold
  are rows.json. GitHub is a fake of example-org's runs and jobs. It is 12:00 UTC on
  2027-01-15, and times are in minutes from then.

  Background:
    Given a scratch directory
    And storage for CI's usage

  Scenario: The poller writes each finished job once, with its wait
    Given a finished run 7, created 40 minutes ago and updated 5 minutes ago, with the jobs:
      | id | queued | started | done | labels        | runner                | runner_group_name |
      | 1  | -30    | -20     | -10  | sustenaut-ci  | sustenaut-ci-runner-a | private           |
      | 2  | -60    | -25     | -12  | ubuntu-latest | GitHub Actions 3      | GitHub Actions    |
    When the poller makes a pass
    Then the result is "2"
    And the rows written are:
      | job_id | wait_seconds | run_seconds | runner_group   | labels            | repository      | event        | head_branch |
      | 1      | 600          | 600         | private        | ["sustenaut-ci"]  | example-org/app | pull_request | main        |
      | 2      | 2100         | 780         | GitHub Actions | ["ubuntu-latest"] | example-org/app | pull_request | main        |
    And the directory "buffer/inbox/ci-jobs/2027-01-15" holds files named:
      """
      120000-ci-jobs-20270115-*.avro
      """

  Scenario: The poller saves what it wrote, and the run it finished with
    Given a finished run 7, created 40 minutes ago and updated 5 minutes ago, with the jobs:
      | id | queued | started | done |
      | 1  | -30    | -20     | -10  |
      | 2  | -60    | -25     | -12  |
    When the poller makes a pass
    Then the JSON in "state.json" is:
      """
      {"written": {"1": "2027-01-15T11:50:00Z", "2": "2027-01-15T11:48:00Z"},
       "finished": {"7": "2027-01-15T11:20:00Z|2027-01-15T11:55:00Z"}}
      """

  Scenario: A second pass writes nothing, saves nothing and reads no finished run again
    Given a finished run 7, created 40 minutes ago and updated 5 minutes ago, with the jobs:
      | id | queued | started | done |
      | 1  | -30    | -20     | -10  |
    And the poller has made a pass
    When the poller makes a pass 10 minutes later
    Then the result is "0"
    And nothing was written
    And GitHub was asked for no run's jobs since the pass began

  Scenario: A run still in progress is not finished with, and only its finished job is written
    Given a run 7 in progress, created 40 minutes ago and updated 5 minutes ago, with the jobs:
      | id | status      | queued | started | done |
      | 1  | completed   | -30    | -20     | -10  |
      | 2  | in_progress | -30    | -20     | none |
    When the poller makes a pass
    Then the result is "1"
    And the JSON in "state.json" has "finished" equal to "{}"
    And the rows written are:
      | job_id |
      | 1      |

  Scenario: A job still running is written once it completes
    Given a run 7 in progress, created 40 minutes ago and updated 5 minutes ago, with the jobs:
      | id | status      | queued | started | done |
      | 1  | completed   | -30    | -20     | -10  |
      | 2  | in_progress | -30    | -20     | none |
    And the poller has made a pass
    And run 7 has since finished, updated 5 minutes from now, with its job 2 done 4 minutes from now
    When the poller makes a pass 10 minutes later
    Then the result is "1"
    And the rows written are:
      | job_id |
      | 1      |
      | 2      |

  Scenario: A job no runner took has no wait
    Given a finished run 7, created 40 minutes ago and updated 5 minutes ago, with the jobs:
      | id | queued | started | done | runner | conclusion |
      | 1  | -30    | none    | -10  | none   | cancelled  |
    When the poller makes a pass
    Then the rows written are:
      | job_id | wait_seconds | started_at | runner_name | conclusion |
      | 1      | null         | null       | null        | cancelled  |

  Scenario: A rate limit is waited out
    Given a finished run 7, created 40 minutes ago and updated 5 minutes ago, with the jobs:
      | id | queued | started | done |
      | 1  | -30    | -20     | -10  |
    And GitHub first answers 403 with:
      """
      {"message": "API rate limit exceeded for installation"}
      """
    When the poller makes a pass
    Then the result is "1"
    And it slept 60 seconds, once

  Scenario: A refusal that is no rate limit stops the pass before anything is saved
    Given GitHub first answers 404 with:
      """
      {"message": "Not Found"}
      """
    When the poller makes a pass
    Then it is refused, saying "HTTP 404"
    And nothing was written

  Scenario: A save the CronJob got in first is merged with it and tried again
    Given a finished run 7, created 40 minutes ago and updated 10 minutes ago, with the jobs:
      | id | queued | started | done |
      | 7  | -39    | -38     | -20  |
    And a finished run 8, created 1480 minutes ago and updated 1450 minutes ago, with the jobs:
      | id | queued | started | done  |
      | 8  | -1479  | -1478   | -1460 |
    And the ten-minute CronJob saves job 99 each time the pass writes a file
    When the poller makes a pass
    Then the result is "2"
    And the JSON in "state.json" has "written" equal to "{"7": "2027-01-15T11:40:00Z", "8": "2027-01-14T11:40:00Z", "99": "2027-01-15T11:59:00Z"}"

  Scenario: A save refused every time stops after a few tries
    Given the ten-minute CronJob saves before each move of the head
    When the poller saves the state of job 1
    Then it is refused, saying "412"
    And the head was asked to move 5 times

  Scenario: A refusal that is no conflict is not tried again
    Given storage refuses to move the head with 403
    When the poller saves the state of job 1
    Then it is refused, saying "403"
    And the directory "uploads" holds:
      """
      h0
      """

  Scenario: A state of 90 days of jobs is pruned to the look back when it is saved
    Given the head holds 90 days of jobs, one each 10 minutes, three times over
    And a finished run 7, created 40 minutes ago and updated 5 minutes ago, with the jobs:
      | id     | queued | started | done |
      | 999999 | -30    | -20     | -10  |
    When the poller makes a pass
    Then the result is "1"
    And the file "state.json" is smaller than 1048576 bytes
    And the JSON in "state.json" has "written.999999" equal to "2027-01-15T11:50:00Z"
    And the JSON in "state.json" has "written.1" equal to "2027-01-15T11:50:00Z"
    And every job in "state.json" was written in the 4 days before now

  Scenario: A job listed again inside the look back is not written twice
    Given a finished run 7, created 2880 minutes ago and updated 2850 minutes ago, with the jobs:
      | id | queued | started | done  |
      | 1  | -2879  | -2878   | -2860 |
    And the poller has made a pass
    And run 7 has since been re-run, updated 5 minutes from now, with the new job:
      | id | queued | started | done |
      | 2  | 1      | 2       | 4    |
    When the poller makes a pass 10 minutes later
    Then the result is "1"
    And the rows written are:
      | job_id |
      | 1      |
      | 2      |

  Scenario: A backfill writes a run two days' queries list only once
    Given a finished run 9, created 43920 minutes ago and updated 43890 minutes ago, with the jobs:
      | id | queued | started | done   |
      | 9  | -43920 | -43919  | -43900 |
    When the poller makes a backfill pass of 90 days
    Then the result is "1"
    And the rows written are:
      | job_id |
      | 9      |

  Scenario: A backfill writes a file for each day of runs, and keeps only the look back's
    Given a finished run 7, created 40 minutes ago and updated 10 minutes ago, with the jobs:
      | id | queued | started | done |
      | 7  | -39    | -38     | -20  |
    And a finished run 8, created 2920 minutes ago and updated 2890 minutes ago, with the jobs:
      | id | queued | started | done  |
      | 8  | -2919  | -2918   | -2900 |
    And a finished run 9, created 57640 minutes ago and updated 57610 minutes ago, with the jobs:
      | id | queued | started | done   |
      | 9  | -57639 | -57638  | -57620 |
    When the poller makes a backfill pass of 90 days
    Then the result is "3"
    And the directory "buffer/inbox/ci-jobs/2027-01-15" holds files named:
      """
      120000-ci-jobs-20261206-*.avro
      120000-ci-jobs-20270113-*.avro
      120000-ci-jobs-20270115-*.avro
      """
    And the JSON in "state.json" has "written" equal to "{"7": "2027-01-15T11:40:00Z", "8": "2027-01-13T11:40:00Z"}"

  Scenario: The board's repositories come from state.json
    When the board is read from release-state's state.json
    Then the result is:
      """
      ["example-org/app"]
      """

  Scenario: The poller reads every repository the installation sees, archived ones aside
    When the installation's repositories are read
    Then the result is:
      """
      ["example-org/app", "example-org/infra"]
      """

  Scenario: The backfill's wait ends when the Job completes
    Given an "ssh" that runs what it is sent in a local shell
    And a stand-in "k3s" that answers in turn:
      | args                                                  | stdout                                                                                          |
      | kubectl -n integrations get job ci-jobs-backfill -o json | {"status": {"conditions": [{"type": "Suspended", "status": "False"}]}}                       |
      | kubectl -n integrations get job ci-jobs-backfill -o json | {"status": {"conditions": [{"type": "Suspended", "status": "False"}]}}                       |
      | kubectl -n integrations get job ci-jobs-backfill -o json | {"status": {"conditions": [{"type": "Complete", "status": "True"}, {"type": "Suspended", "status": "False"}]}} |
    And the environment sets "WAIT_SECONDS" to "0"
    And a file "wait-for-job.sh" copied from "scripts/wait-for-job.sh"
    And the file "wait-for-job.sh" is executable
    When I run "./wait-for-job.sh integrations ci-jobs-backfill"
    Then it succeeds
    And stderr is empty
    And "k3s" was run 3 times

  Scenario: The backfill's wait fails with the end of its log, without the call records, when the Job fails
    Given an "ssh" that runs what it is sent in a local shell
    And a stand-in "k3s" that answers in turn:
      | args                                                  | stdout                                                                                       |
      | kubectl -n integrations get job ci-jobs-backfill -o json | {"status": {"conditions": [{"type": "Suspended", "status": "False"}]}}                    |
      | kubectl -n integrations get job ci-jobs-backfill -o json | {"status": {"conditions": [{"type": "Failed", "status": "True"}, {"type": "Suspended", "status": "False"}]}} |
      | kubectl -n integrations logs job/ci-jobs-backfill      | {"call":"record"}\nsaved 40 jobs\nstorage.api write: HTTP 412 written moved just now         |
    And the environment sets "WAIT_SECONDS" to "0"
    And a file "wait-for-job.sh" copied from "scripts/wait-for-job.sh"
    And the file "wait-for-job.sh" is executable
    When I run "./wait-for-job.sh integrations ci-jobs-backfill"
    Then the exit code is 1
    And stderr mentions "Job ci-jobs-backfill failed"
    And stderr mentions "HTTP 412"
    And stderr does not mention "{"call""

  Scenario: The sampler writes node, namespace, ci and runner rows
    Given a cluster of one node, with a runner pod, an app pod and a finished pod
    And the runner's container peaked at 734003200 bytes and its dind at 52428800
    When the sampler takes a sample
    Then the result is "5"
    And the directory "buffer/inbox/cluster-usage/2027-01-15" holds files named:
      """
      120000-usage-*
      """
    And the rows written are:
      | kind      | namespace | cpu_millicores | cpu_capacity_millicores | memory_capacity_bytes |
      | node      | null      | 472            | 4000                    | 8267177984            |
      | namespace | auth      | 3              | null                    | null                  |
      | namespace | ci        | 155            | null                    | null                  |
      | ci        | ci        | null           | null                    | null                  |
      | runner    | ci        | 155            | null                    | null                  |
    And the JSON in "rows.json" has "1.memory_requests_bytes" equal to "1363148800"
    And the JSON in "rows.json" has "1.cpu_requests_millicores" equal to "100"
    And the JSON in "rows.json" has "2.cpu_requests_millicores" equal to "250"
    And the JSON in "rows.json" has "2.memory_bytes" equal to "131072000"
    And the JSON in "rows.json" has "3.quota_memory_used" equal to "805306368"
    And the JSON in "rows.json" has "3.quota_memory_hard" equal to "3758096384"
    And the JSON in "rows.json" has "3.runners_running" equal to "1"
    And the JSON in "rows.json" has "3.runners_pending" equal to "1"
    And the JSON in "rows.json" has "3.runners_max" equal to "4"
    And the JSON in "rows.json" has "4.runner_name" equal to "sustenaut-ci-runner-a"
    And the JSON in "rows.json" has "4.node" equal to "sustenaut"
    And the JSON in "rows.json" has "4.runner_memory_peak_bytes" equal to "734003200"
    And the JSON in "rows.json" has "4.dind_memory_peak_bytes" equal to "52428800"

  Scenario Outline: Kubernetes CPU quantities read as millicores
    When the CPU quantity "<quantity>" is read
    Then the result is "<millicores>"

    Examples:
      | quantity   | millicores |
      | 2          | 2000       |
      | 250m       | 250        |
      | 1.5        | 1500       |
      | 123456789n | 123        |
      | 3600u      | 4          |

  Scenario Outline: Kubernetes memory quantities read as bytes
    When the memory quantity "<quantity>" is read
    Then the result is "<bytes>"

    Examples:
      | quantity | bytes      |
      | 512Mi    | 536870912  |
      | 1Gi      | 1073741824 |
      | 123Ki    | 125952     |
      | 1e3      | 1000       |
      | 2G       | 2000000000 |
      | 100      | 100        |

  Scenario Outline: No quantity reads as none
    When no <kind> quantity is read
    Then the result is empty

    Examples:
      | kind   |
      | CPU    |
      | memory |

  Scenario: A container with no cgroup has no peak
    When the memory peak of a container with no cgroup is read
    Then the result is empty

  Scenario: No container has no peak
    When the memory peak of no container is read
    Then the result is empty

  Scenario: CI job rows are the same bytes as their copies, and read back as they were
    When two CI job rows are encoded, as they are and as copies, and read back
    Then the result's "same bytes" is "true"
    And the result's "read back as they were" is "true"

  Scenario: Cluster usage is the same bytes as its copy, and a field not given reads as none
    When a cluster usage row is encoded, as it is and as a copy, and read back
    Then the result's "same bytes" is "true"
    And the result's "read back.0.cpu_millicores" is "5"
    And the result's "read back.0.quota_cpu_hard" is "null"
