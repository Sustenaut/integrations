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

  Scenario: A backfill writes a file for each day of runs
    Then a backfill writes a file for each day of runs

  Scenario: The board's repositories come from state.json
    Then the board's repositories come from state.json

  Scenario: The sampler writes node, namespace, ci and runner rows
    Then the sampler writes node, namespace, ci and runner rows

  Scenario: Kubernetes quantities read as millicores and bytes
    Then kubernetes quantities read as millicores and bytes

  Scenario: CI jobs and cluster usage are the same bytes for the same rows
    Then ci jobs and cluster usage are the same bytes for the same rows
