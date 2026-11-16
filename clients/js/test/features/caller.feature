@caller
Feature: clients/js/caller.js: what tests/vectors/ doesn't cover

  Scenario: The tables are classes.json's and the plugins'
    Then the tables are classes.json's and the plugins'

  Scenario: A record has CallRecord's fields, in its order, and no body, URL or token
    Then a record has CallRecord's fields, in its order, and no body, URL or token

  Scenario: An operation not in the registry is refused, for a call and a note
    Then an operation not in the registry is refused, for a call and a note

  Scenario: The answer can be read as the client needs it
    Then the answer can be read as the client needs it

  Scenario: The call is sent with its method, headers, body, a User-Agent and a class timeout
    Then the call is sent with its method, headers, body, a User-Agent and a class timeout

  Scenario: A provider that doesn't answer within its class's timeout is no answer
    Then a provider that doesn't answer within its class's timeout is no answer

  Scenario: Only https goes out
    Then only https goes out

  Scenario: The backoff sleeps by default, in seconds
    Then the backoff sleeps by default, in seconds

  Scenario: A note is a record of time that wasn't a call
    Then a note is a record of time that wasn't a call

  Scenario: forRepo is the same caller for another project
    Then forRepo is the same caller for another project

  Scenario: A sink that fails loses the record, not the call
    Then a sink that fails loses the record, not the call

  Scenario: The default sink keeps nothing, and the default fetch is the global one
    Then the default sink keeps nothing, and the default fetch is the global one

  Scenario: Refused is for clients, as in Python
    Then Refused is for clients, as in Python

  Scenario: Modes come from integrations.json, required winning
    Then modes come from integrations.json, required winning

  Scenario: A replay answers in turn, keeps its last answer, and a null-body status has none
    Then a replay answers in turn, keeps its last answer, and a null-body status has none

  Scenario: Json is written as Python's json.dumps writes it
    Then json is written as Python's json.dumps writes it

  Scenario: A Retry-After date is waited for up to the timeout, and one in the past not at all
    Then a Retry-After date is waited for up to the timeout, and one in the past not at all

  Scenario: A call never runs past its timeout times its tries
    Then a call never runs past its timeout times its tries

  Scenario: No try starts that the deadline leaves no time for
    Then no try starts that the deadline leaves no time for

  Scenario: The jitter's random source is the caller's, and forRepo keeps it
    Then the jitter's random source is the caller's, and forRepo keeps it
