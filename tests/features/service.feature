Feature: The running service, with GitHub on recorded answers.

  Scenario: A proposal is one commit and a pull request as the person
    Then a proposal is one commit and a pull request as the person

  Scenario: Parent base and message are the callers
    Then parent base and message are the callers

  Scenario: No change and the same tree are nothing to change
    Then no change and the same tree are nothing to change

  Scenario: A file that moved is stale and writes nothing
    Then a file that moved is stale and writes nothing

  Scenario: A file that cant be read for expect is refused
    Then a file that cant be read for expect is refused

  Scenario: Deleting skips a file that isnt there
    Then deleting skips a file that isnt there

  Scenario: Auto merge says what happened and the pull request stands
    Then auto merge says what happened and the pull request stands

  Scenario: A repository not named is refused before any call
    Then a repository not named is refused before any call

  Scenario: With nothing named nothing is served
    Then with nothing named nothing is served

  Scenario: Settings come from the environment
    Then settings come from the environment

  Scenario: No token is refused
    Then no token is refused

  Scenario: Githubs refusal comes back with its words
    Then githubs refusal comes back with its words

  Scenario: A refusal with no json still says its status
    Then a refusal with no json still says its status

  Scenario: Github not answering is a 503 and a bad answer a 502
    Then github not answering is a 503 and a bad answer a 502

  Scenario: The token is never in a record a log or an answer
    Then the token is never in a record a log or an answer

  Scenario: The service keeps no token
    Then the service keeps no token

  Scenario: A stdout record holds no token
    Then a stdout record holds no token

  Scenario: Cors lets in only the named sites
    Then cors lets in only the named sites

  Scenario: The rate limit is per minute and refuses before any call
    Then the rate limit is per minute and refuses before any call

  Scenario: A rate limit lets calls in again after its window
    Then a rate limit lets calls in again after its window

  Scenario: A body too large is refused before it is read
    Then a body too large is refused before it is read

  Scenario: A bad proposal is a validation error
    Then a bad proposal is a validation error

  Scenario: The address alone leads to the docs
    Then the address alone leads to the docs

  Scenario: A projects calls are recorded as that projects
    Then a projects calls are recorded as that projects

  Scenario: Healthz needs nothing
    Then healthz needs nothing

  Scenario: A buffer it cannot reach only alerts
    Then a buffer it cannot reach only alerts

  Scenario: The service checks its storage token
    Then the service checks its storage token

  Scenario: The built docs are served and the interactive ones are off
    Then the built docs are served and the interactive ones are off

  Scenario: A snapshot is the newest commit its paths and whether the token may write
    Then a snapshot is the newest commit its paths and whether the token may write

  Scenario: A snapshot says when the token may not write and when github left paths out
    Then a snapshot says when the token may not write and when github left paths out

  Scenario: A snapshot can be of another branch
    Then a snapshot can be of another branch

  Scenario: Files come in one call with null for one that isnt there
    Then files come in one call with null for one that isnt there

  Scenario: A file github refuses refuses the call
    Then a file github refuses refuses the call

  Scenario: Too many files is a validation error
    Then too many files is a validation error

  Scenario: Reads serve only the repositories named and need a token
    Then reads serve only the repositories named and need a token

  Scenario: Reads have their own rate limit
    Then reads have their own rate limit

  Scenario: Reads keep the token out of records logs and answers
    Then reads keep the token out of records logs and answers

  Scenario: Cors lets the named sites read
    Then cors lets the named sites read

  Scenario: Every call of one request shares its id and the answer says it
    Then every call of one request shares its id and the answer says it

  Scenario: An apps own id is kept unless it isnt one
    Then an apps own id is kept unless it isnt one

  Scenario: A request records its whole time as well as its calls
    Then a request records its whole time as well as its calls

  Scenario: A failed request records how it ended
    Then a failed request records how it ended

  Scenario: An apps timings become records with no token needed
    Then an apps timings become records with no token needed

  Scenario: Timings are only ids and sensible numbers
    Then timings are only ids and sensible numbers

  Scenario: Timings serve only the repositories named and are rate limited
    Then timings serve only the repositories named and are rate limited

  Scenario: Timings with no token cant use up what reads and proposals need
    Then timings with no token cant use up what reads and proposals need

  Scenario: The committed spec is what the service gives
    Then the committed spec is what the service gives

  Scenario: Records go to storage only when a deployment names it
    Then records go to storage only when a deployment names it
