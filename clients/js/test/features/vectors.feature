@vectors
Feature: The vectors in tests/vectors/, which tests/steps/test_vectors.py runs on calls.py too

  Scenario: caller.js gives every vector in tests/vectors/ what it expects
    Then caller.js gives every vector in tests/vectors/ what it expects

  Scenario: There are vectors to run
    Then there are vectors to run, in tests/vectors/
