Feature: A sender's files: one every cycle, even with nothing in it, and nothing lost or doubled.

  Scenario: An idle cycle still writes a report
    Then an idle cycle still writes a report

  Scenario: A cycles calls are one file
    Then a cycles calls are one file

  Scenario: Each project has its own tenant
    Then each project has its own tenant

  Scenario: Past the limit the oldest go and are counted
    Then past the limit the oldest go and are counted

  Scenario: A record for a project with no buffer is counted
    Then a record for a project with no buffer is counted

  Scenario: Writing a record never fails with no projects
    Then writing a record never fails with no projects

  Scenario: A refused file goes again as it was and the failures are reported
    Then a refused file goes again as it was and the failures are reported

  Scenario: A lost answer cant double a files calls
    Then a lost answer cant double a files calls

  Scenario: A token that cant be read is a failed write
    Then a token that cant be read is a failed write

  Scenario: From a call to the tables
    Then from a call to the tables

  Scenario: With flush on write a request that finds the file overdue writes it
    Then with flush on write a request that finds the file overdue writes it

  Scenario: Without it writing never waits on storage
    Then without it writing never waits on storage

  Scenario: A write doesnt flush while another flush is under way
    Then a write doesnt flush while another flush is under way

  Scenario: A write storage didnt take is said in the log
    Then a write storage didnt take is said in the log

  Scenario: A refusal says storages reason and a missing token says why
    Then a refusal says storages reason and a missing token says why

  Scenario: A refusal without a reason still says its status
    Then a refusal without a reason still says its status

  Scenario: Page times become the windows low median and high
    Then page times become the windows low median and high

  Scenario: Only the first window is a cold start and a quiet one has no times
    Then only the first window is a cold start and a quiet one has no times

  Scenario: Past the limit page times are still counted
    Then past the limit page times are still counted

  Scenario: Page times reach the status table
    Then page times reach the status table

  Scenario: The threads first file comes a random part of a cycle in
    Then the threads first file comes a random part of a cycle in
