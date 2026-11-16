Feature: The scanner: files in storage reach BigQuery once and leave, whatever stops partway.

  Scenario: Waiting files load as one job and leave
    Then waiting files load as one job and leave

  Scenario: An empty inbox spends no job and still reports
    Then an empty inbox spends no job and still reports

  Scenario: A file with no records just leaves
    Then a file with no records just leaves

  Scenario: A crash after the load doesnt load again
    Then a crash after the load doesnt load again

  Scenario: A crash partway through deleting finishes with what is left
    Then a crash partway through deleting finishes with what is left

  Scenario: A crash while splitting a rejected batch loads nothing twice
    Then a crash while splitting a rejected batch loads nothing twice

  Scenario: A batch whose files have all gone only loses its record
    Then a batch whose files have all gone only loses its record

  Scenario: New files wait while an unfinished batch does
    Then new files wait while an unfinished batch does

  Scenario: A job that failed in a way that may pass goes again as a new job
    Then a job that failed in a way that may pass goes again as a new job

  Scenario: Bad files alone move to rejected
    Then bad files alone move to rejected

  Scenario: Finding a bad file takes few jobs
    Then finding a bad file takes few jobs

  Scenario: A file that isnt avro or isnt call records is rejected without a job
    Then a file that isnt avro or isnt call records is rejected without a job

  Scenario: Rows that cant be call records are rejected
    Then rows that cant be call records are rejected

  Scenario: The days job limit keeps files waiting
    Then the days job limit keeps files waiting

  Scenario: A batch is kept to what fits in memory
    Then a batch is kept to what fits in memory

  Scenario: Storage down leaves everything and says so
    Then storage down leaves everything and says so

  Scenario: A batch record that cant be written means no load
    Then a batch record that cant be written means no load

  Scenario: A refusal says what was refused
    Then a refusal says what was refused

  Scenario: One projects bad day doesnt stop the next
    Then one projects bad day doesnt stop the next

  Scenario: Backlog is reported with its age
    Then backlog is reported with its age

  Scenario: A senders report reaches the status table and not calls
    Then a senders report reaches the status table and not calls

  Scenario: An idle senders file has a report and spends no calls job
    Then an idle senders file has a report and spends no calls job

  Scenario: Many reports are one job
    Then many reports are one job

  Scenario: The pod reports each cycle and it loads on the next
    Then the pod reports each cycle and it loads on the next

  Scenario: The pods report says what went wrong
    Then the pods report says what went wrong

  Scenario: Reports arent loaded twice when the calls go again
    Then reports arent loaded twice when the calls go again

  Scenario: A crash after the reports loaded doesnt load them again
    Then a crash after the reports loaded doesnt load them again

  Scenario: A report job that failed goes again before the calls
    Then a report job that failed goes again before the calls

  Scenario: Rejected reports dont hold up the calls
    Then rejected reports dont hold up the calls

  Scenario: A report that isnt one only loses the report
    Then a report that isnt one only loses the report

  Scenario: The status table has its own days jobs
    Then the status table has its own days jobs

  Scenario: Signals load into their own table from their own tenant and leave
    Then signals load into their own table from their own tenant and leave

  Scenario: A file in the signals inbox that isnt signals is rejected
    Then a file in the signals inbox that isnt signals is rejected

  Scenario: Signals are the same bytes for the same rows
    Then signals are the same bytes for the same rows

  Scenario: Ingest signals names the tenant and dataset
    Then ingest signals names the tenant and dataset

  Scenario: A wrong ingest signals is refused
    Then a wrong ingest signals is refused

  Scenario: Need states load into their own table from their own tenant and leave
    Then need states load into their own table from their own tenant and leave

  Scenario: Need states are the same bytes for the same rows
    Then need states are the same bytes for the same rows

  Scenario: Ingest need states names its tenant and dataset beside the signals
    Then ingest need states names its tenant and dataset beside the signals

  Scenario: Pubkit checks load into their own table from their own tenant and leave
    Then pubkit checks load into their own table from their own tenant and leave

  Scenario: Pubkit checks are the same bytes for the same rows
    Then pubkit checks are the same bytes for the same rows

  Scenario: Ingest pubkit checks names its tenant and dataset beside the others
    Then ingest pubkit checks names its tenant and dataset beside the others

  Scenario: The scan passes once at start and then each interval
    Then the scan passes once at start and then each interval
