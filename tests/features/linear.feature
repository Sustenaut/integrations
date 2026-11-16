Feature: The Linear client, against recorded answers.

  Scenario: A query answers its data and sends the key as linear wants
    Then a query answers its data and sends the key as linear wants

  Scenario: Operation says who is waiting
    Then operation says who is waiting

  Scenario: Errors beside a 200 and in a 400 are refusals with linears reason
    Then errors beside a 200 and in a 400 are refusals with linears reason

  Scenario: A refusal with a body that is not json still says its status
    Then a refusal with a body that is not json still says its status

  Scenario: A query is retried on a timer and a mutation is not
    Then a query is retried on a timer and a mutation is not

  Scenario: A query a person waits on is not retried
    Then a query a person waits on is not retried

  Scenario: Check says whether the key works
    Then check says whether the key works

  Scenario: The fake answers in turn and keeps what was asked
    Then the fake answers in turn and keeps what was asked
