Feature: The defaults pubrules.json gives the ingest pod's variables are the code's
  pubrules.json's variables block (version 2) gives INGEST_SWEEP_SECONDS,
  INGEST_REPORT_SECONDS and INGEST_BATCH_GAP_SECONDS a default each, so the deploy
  walk finds them ready unset. A default says what the code does when the variable
  is unset, so it must be the code's own constant: the one main.py falls back to. And
  as deploy.yml renders an unset variable as "", the code must read "" as unset too.

  Scenario: Each default is the constant the code falls back to, unset or empty
    When I read the defaults pubrules.json gives and what the code does without the variables
    Then the result is:
      """
      {"INGEST_SWEEP_SECONDS": {"default": "120", "is the constant": true, "unset gives": 120, "empty gives": 120, "deploy.yml renders it": true},
       "INGEST_REPORT_SECONDS": {"default": "120", "is the constant": true, "unset gives": 120, "empty gives": 120, "deploy.yml renders it": true},
       "INGEST_BATCH_GAP_SECONDS": {"default": "120", "is the constant": true, "unset gives": 120, "empty gives": 120, "deploy.yml renders it": true}}
      """

  Scenario: The block is version 2, and defaults only those three
    When I read pubrules.json's variables block
    Then the result is:
      """
      {"version": 2, "defaulted": ["INGEST_BATCH_GAP_SECONDS", "INGEST_REPORT_SECONDS", "INGEST_SWEEP_SECONDS"],
       "every defaulted name is declared": true, "problems": []}
      """
