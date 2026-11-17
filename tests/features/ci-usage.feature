Feature: CI's wait times and compute load
  The ci-jobs poller writes each finished GitHub Actions job of the board's repositories
  once, with how long it waited for a runner; the sampler writes the server's usage and
  each runner pod's peak memory. Both go to storage's ci-usage buffer.

  Scenario: The poller writes each finished job once, with its wait, and saves what it wrote
    Then the poller writes each finished job once, with its wait, and saves what it wrote

  Scenario: A second pass writes nothing and saves nothing
    Then a second pass writes nothing and saves nothing

  Scenario: A job still running is written once it completes
    Then a job still running is written once it completes

  Scenario: A job no runner took has no wait
    Then a job no runner took has no wait

  Scenario: A rate limit is waited out
    Then a rate limit is waited out

  Scenario: A refusal that is no rate limit stops the pass before anything is saved
    Then a refusal that is no rate limit stops the pass before anything is saved

  Scenario: A save the CronJob got in first is merged with it and tried again
    Then a save the CronJob got in first is merged with it and tried again

  Scenario: A save refused every time stops after a few tries
    Then a save refused every time stops after a few tries

  Scenario: A refusal that is no conflict is not tried again
    Then a refusal that is no conflict is not tried again

  Scenario: The backfill's wait ends when the Job completes
    Then the backfill's wait ends when the Job completes

  Scenario: The backfill's wait fails with the end of its log when the Job fails
    Then the backfill's wait fails with the end of its log when the Job fails

  Scenario: A state of 90 days of jobs is pruned to the look back when it is saved
    Then a state of 90 days of jobs is pruned to the look back when it is saved

  Scenario: A job listed again inside the look back is not written twice
    Then a job listed again inside the look back is not written twice

  Scenario: A backfill writes a run two days' queries list only once
    Then a backfill writes a run two days' queries list only once

  Scenario: A backfill writes a file for each day of runs
    Then a backfill writes a file for each day of runs

  Scenario: The board's repositories come from state.json
    Then the board's repositories come from state.json

  Scenario: The poller reads every repository the installation sees, archived ones aside
    Then the poller reads every repository the installation sees, archived ones aside

  Scenario: The sampler writes node, namespace, ci and runner rows
    Then the sampler writes node, namespace, ci and runner rows

  Scenario: Kubernetes quantities read as millicores and bytes
    Then kubernetes quantities read as millicores and bytes

  Scenario: CI jobs and cluster usage are the same bytes for the same rows
    Then ci jobs and cluster usage are the same bytes for the same rows
