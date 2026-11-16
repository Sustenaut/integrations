Feature: The wrapper every client call goes through, and the first clients.

  Scenario: A record says who called what and how it ended
    Then a record says who called what and how it ended

  Scenario: A record holds no body url or token
    Then a record holds no body url or token

  Scenario: An app may give the request id so a slow save can be traced
    Then an app may give the request id so a slow save can be traced

  Scenario: An operation not in the registry is refused
    Then an operation not in the registry is refused

  Scenario: A refusal comes back to the client and is recorded
    Then a refusal comes back to the client and is recorded

  Scenario: No answer or a failure raises and is recorded
    Then no answer or a failure raises and is recorded

  Scenario: A call a person waits on is not retried
    Then a call a person waits on is not retried

  Scenario: A scheduled call is retried with backoff and the retries recorded
    Then a scheduled call is retried with backoff and the retries recorded

  Scenario: A call not safe to repeat is not retried unless it says so
    Then a call not safe to repeat is not retried unless it says so

  Scenario: A body that cant be read is left to the client
    Then a body that cant be read is left to the client

  Scenario: A note is a record of time that wasnt a call
    Then a note is a record of time that wasnt a call

  Scenario: A refusal is not retried
    Then a refusal is not retried

  Scenario: A sink that fails loses the record not the call
    Then a sink that fails loses the record not the call

  Scenario: The default sink keeps nothing and the file sink appends
    Then the default sink keeps nothing and the file sink appends

  Scenario: The network transport says who it is as cloudflare refuses urllibs name
    Then the network transport says who it is as cloudflare refuses urllibs name

  Scenario: The network transport sends only over https
    Then the network transport sends only over https

  Scenario: A recording can be replayed and keeps no token
    Then a recording can be replayed and keeps no token

  Scenario: An empty body reads as nothing
    Then an empty body reads as nothing

  Scenario: Mode comes from the repositorys file and reaches the record
    Then mode comes from the repositorys file and reaches the record

  Scenario: Required down is unhealthy and advisory down only alerts
    Then required down is unhealthy and advisory down only alerts

  Scenario: The version is the same in both places
    Then the version is the same in both places

  Scenario: A plugin with a client has a settings schema
    Then a plugin with a client has a settings schema

  Scenario: An edit becomes a pull request on the next free branch
    Then an edit becomes a pull request on the next free branch

  Scenario: An edit of a file that moved is a conflict and changes nothing
    Then an edit of a file that moved is a conflict and changes nothing

  Scenario: A new file that now exists is a conflict
    Then a new file that now exists is a conflict

  Scenario: A refusal from github is refused and names no query
    Then a refusal from github is refused and names no query

  Scenario: Check says whether the repository can be read
    Then check says whether the repository can be read

  Scenario: The branch number ignores other names
    Then the branch number ignores other names

  Scenario: The fake answers as the client does
    Then the fake answers as the client does

  Scenario: A token is minted once and reused until well before it expires
    Then a token is minted once and reused until well before it expires

  Scenario: A refused token is refused and check says so
    Then a refused token is refused and check says so

  Scenario: The fake token signs nothing
    Then the fake token signs nothing

  Scenario: The network transport reads an answer a refusal and silence
    Then the network transport reads an answer a refusal and silence

  Scenario: A caller for a project records that project
    Then a caller for a project records that project

  Scenario: The wait is random up to 2 to the n
    Then the wait is random up to 2 to the n

  Scenario: A retry after date is waited for up to the timeout
    Then a retry after date is waited for up to the timeout

  Scenario: A call never runs past its timeout times its tries
    Then a call never runs past its timeout times its tries

  Scenario: No try starts that the deadline leaves no time for
    Then no try starts that the deadline leaves no time for
