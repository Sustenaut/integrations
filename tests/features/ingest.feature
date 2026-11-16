Feature: The ingest pod's parts: its BigQuery loads, its Google token, its Avro and its health.

  Scenario: Avro is the same bytes for the same records and keeps every field
    Then avro is the same bytes for the same records and keeps every field

  Scenario: A load says whether another try may work
    Then a load says whether another try may work

  Scenario: A lost answer finds the job already there
    Then a lost answer finds the job already there

  Scenario: Ingest answers health and has no public docs
    Then ingest answers health and has no public docs

  Scenario: A scanner that stops makes the pod unready but not dead
    Then a scanner that stops makes the pod unready but not dead

  Scenario: The record fields are the schemas
    Then the record fields are the schemas

  Scenario: A google token is traded in once and reused
    Then a google token is traded in once and reused

  Scenario: A refused google token is a refusal
    Then a refused google token is a refusal

  Scenario: A federated token trades the pods token then impersonates and is reused
    Then a federated token trades the pods token then impersonates and is reused

  Scenario: A federated token refused at either step is a refusal and a bad credential is said
    Then a federated token refused at either step is a refusal and a bad credential is said

  Scenario: The ingests token its credential and the provider name one audience
    Then the ingests token its credential and the provider name one audience
