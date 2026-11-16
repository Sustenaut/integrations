@storage
Feature: clients/js/storage.js, on the vectors in tests/vectors/storage/ that tests/steps/test_storage_client.py runs too, and what they don't cover

  Scenario: storage.js gives every vector in tests/vectors/storage/ what it expects
    Then storage.js gives every vector in tests/vectors/storage/ what it expects

  Scenario: There are vectors to run
    Then there are vectors to run

  Scenario: The routes are the pinned spec's
    Then the routes are the pinned spec's

  Scenario: An upload is one form with the fields given
    Then an upload is one form with the fields given

  Scenario: The token file is read for each request, and another tenant shares it
    Then the token file is read for each request, and another tenant shares it

  Scenario: A token can be a function, and one is needed, as is a tenant
    Then a token can be a function, and one is needed, as is a tenant

  Scenario: A place outside the buffers' folders is refused here
    Then a place outside the buffers' folders is refused here

  Scenario: A batch's JSON goes as it is, and the errors are the caller's kinds
    Then a batch's JSON goes as it is, and the errors are the caller's kinds
