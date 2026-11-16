Feature: The registry and the consumer format, held to their JSON schemas.

  Scenario: Plugin meets the schema
    Then plugin meets the schema

  Scenario: Every signature compiles
    Then every signature compiles

  Scenario: A plugin with no signatures must be manual only
    Then a plugin with no signatures must be manual only

  Scenario: Every inventory row has its plugins and every plugin a row
    Then every inventory row has its plugins and every plugin a row

  Scenario: Every class has its targets
    Then every class has its targets

  Scenario: The registry names no consumer
    Then the registry names no consumer

  Scenario: The loader and the schema accept the same
    Then the loader and the schema accept the same

  Scenario: The loader and the schema refuse the same
    Then the loader and the schema refuse the same

  Scenario: Path patterns
    Then path patterns
