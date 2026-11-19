Feature: Comparing a repository's calls with its integrations.json

  Background:
    Given a scratch directory
    And the program under test is "integrations"
    And the environment does not set "GITHUB_ACTIONS"

  Scenario: Everything used is declared
    Given a file "tools/post.py" holding "requests.post('https://slack.com/api/x')"
    And its integrations.json declares "slack.post" in "tools/post.py"
    When I run "integrations scan ."
    Then it succeeds
    And it prints "all accounted for"

  Scenario: A signature hit that is not declared fails, naming the file and line
    Given a file "tools/post.py" holding "url = 'https://api.linear.app/graphql'"
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it fails
    And it prints "tools/post.py:1: undeclared: api.linear.app (linear.sync)"

  Scenario: A use declared for another file does not cover this one
    Given a file "tools/a.py" holding "LINEAR_API_KEY"
    And a file "tools/b.py" holding "LINEAR_API_KEY"
    And its integrations.json declares "linear.sync" in "tools/a.py"
    When I run "integrations scan ."
    Then it fails
    And it prints "tools/b.py:1: undeclared: LINEAR_API_KEY"

  Scenario: A declared use with no hit is stale
    Given a file "tools/a.py" holding "print('hello')"
    And its integrations.json declares "linear.sync" in "tools/a.py"
    When I run "integrations scan ."
    Then it fails
    And it prints "stale: linear.sync: nothing in tools/a.py uses it"

  Scenario: One stale path among good ones is reported
    Given a file "tools/a.py" holding "LINEAR_API_KEY"
    And a file "tools/b.py" holding "print('hello')"
    And its integrations.json declares "linear.sync" in "tools/a.py,tools/b.py"
    When I run "integrations scan ."
    Then it fails
    And it prints "stale: linear.sync: nothing in tools/b.py uses it"

  Scenario: A plugin with signatures must name where it is used
    Given a file "a.py" holding "print('hello')"
    And its integrations.json declares "linear.sync" with no paths
    When I run "integrations scan ."
    Then it fails
    And it prints "stale: linear.sync: it names no paths"

  Scenario: An advisory use with no hit warns and passes
    Given a file "a.py" holding "print('hello')"
    And its integrations.json declares "linear.sync" in "a.py" as advisory
    When I run "integrations scan ."
    Then it succeeds
    And it prints "advisory: stale: linear.sync: nothing in a.py uses it"
    And it prints "with 1 advisory findings"

  Scenario: An advisory use may name no paths yet
    Given a file "a.py" holding "print('hello')"
    And its integrations.json declares "linear.sync" as advisory with no paths
    When I run "integrations scan ."
    Then it succeeds
    And it prints "advisory: stale: linear.sync: it names no paths"

  Scenario: A hit outside an advisory use's paths still fails
    Given a file "a.py" holding "LINEAR_API_KEY"
    And a file "b.py" holding "LINEAR_API_KEY"
    And its integrations.json declares "linear.sync" in "a.py" as advisory
    When I run "integrations scan ."
    Then it fails
    And it prints "b.py:1: undeclared: LINEAR_API_KEY"

  Scenario: A required use beside an advisory one still fails
    Given a file "a.py" holding "print('hello')"
    And its integrations.json declares "linear.sync" in "a.py" as advisory
    And its integrations.json declares "anthropic.api" in "a.py"
    When I run "integrations scan ."
    Then it fails
    And it prints "stale: anthropic.api: nothing in a.py uses it"

  Scenario: An advisory finding is an annotation in CI
    Given a file "a.py" holding "print('hello')"
    And its integrations.json declares "linear.sync" in "a.py" as advisory
    And the environment sets "GITHUB_ACTIONS" to "true"
    When I run "integrations scan ."
    Then it succeeds
    And it prints "::warning file=integrations.json::"

  Scenario: A Terraform stack can be advisory
    Given a file "a.py" holding "print('hello')"
    And its integrations.json has an advisory stack "infra" with "terraform.hetzner"
    When I run "integrations scan ."
    Then it succeeds
    And it prints "advisory: stale: terraform.hetzner"

  Scenario: A signature several plugins share is declared by any of them
    Given a file "lib/pr.py" holding "https://api.github.com/repos"
    And its integrations.json declares "github.edit-pr" in "lib/"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A manual plugin needs no hit
    Given a file "a.py" holding "print('hello')"
    And its integrations.json declares "inwx.registrar" in "help/dns.md"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A comment is not a call
    Given a file "a.py" holding "# see https://api.linear.app/graphql"
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A host no plugin names is an unregistered provider
    Given a file "a.py" holding "get('https://api.newvendor.io/v1')"
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it fails
    And it prints "a.py:1: host api.newvendor.io is not registered"

  Scenario: A host may be listed with its reason
    Given a file "a.py" holding "get('https://api.newvendor.io/v1')"
    And its integrations.json lists "other_hosts" entry "host" "api.newvendor.io"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: The repository's own domains are not providers
    Given a file "a.py" holding "get('https://app.mine.example/x')"
    And its integrations.json owns "mine.example"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A name that merely ends like a registered host is not that host
    Given a file "a.py" holding "get('https://notslack.com/x')"
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it fails
    And it prints "host notslack.com is not registered"

  Scenario: A workflow secret no plugin names fails
    Given a file ".github/workflows/a.yml" holding "token: ${{ secrets.NEWVENDOR_TOKEN }}"
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it fails
    And it prints "secret NEWVENDOR_TOKEN is not registered"

  Scenario: A secret may be listed with its reason
    Given a file ".github/workflows/a.yml" holding "token: ${{ secrets.NEWVENDOR_TOKEN }}"
    And its integrations.json lists "other_secrets" entry "name" "NEWVENDOR_TOKEN"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A Terraform provider no plugin names fails
    Given a file "infra/versions.tf" holding:
      """
      source = "newvendor/thing"
      """
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it fails
    And it prints "Terraform provider newvendor/thing is not registered"

  Scenario: A Terraform stack declares its providers for everything under it
    Given a file "infra/versions.tf" holding:
      """
      source = "hetznercloud/hcloud"
      """
    And its integrations.json has a stack "infra" with "terraform.hetzner"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A Terraform provider's source is matched whatever its case
    Given a file "infra/versions.tf" holding:
      """
      source = "Valodim/desec"
      """
    And its integrations.json has a stack "infra" with "terraform.desec"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A GitHub Action is matched by its prefix
    Given a file ".github/workflows/a.yml" holding "- uses: google-github-actions/auth@abc"
    And its integrations.json declares "google.cloud" in ".github/workflows/**"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: An SDK import is a use
    Given a file "draft.py" holding "    import anthropic"
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it fails
    And it prints "draft.py:1: undeclared"

  Scenario: Using a client here is a use of its plugin
    Given a file "app.py" holding "from integrations.clients.github_edit_pr import Change"
    And its integrations.json declares "github.edit-pr" in "app.py"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A file's presence can be a use
    Given a file "with-secrets.json" holding "{}"
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it fails
    And it prints "with-secrets.json:1: undeclared: with-secrets.json (bitwarden.secrets)"

  Scenario: An excluded path is not read
    Given a file "tests/fake.py" holding "https://api.linear.app/graphql"
    And its integrations.json lists "exclude" entry "path" "tests/**"
    When I run "integrations scan ."
    Then it succeeds

  Scenario: Prose and lock files are not read
    Given a file "README.md" holding "https://api.linear.app/graphql"
    And a file "package-lock.json" holding "https://api.linear.app/graphql"
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it succeeds

  Scenario: A script without a suffix is read when it has a shebang
    Given a file "deploy" holding:
      """
      #!/bin/sh
      bws run
      """
    And its integrations.json declares nothing
    When I run "integrations scan ."
    Then it fails
    And it prints "deploy:2: undeclared: bws (bitwarden.secrets)"

  Scenario: No integrations.json is an error, not a pass
    Given a file "a.py" holding "print('hello')"
    When I run "integrations scan ."
    Then the exit code is 2
    And stderr mentions "integrations.json is missing"

  Scenario: A plugin the registry does not have is an error
    Given a file "a.py" holding "print('hello')"
    And its integrations.json declares "nope.nothing" in "a.py"
    When I run "integrations scan ."
    Then the exit code is 2
    And stderr mentions "nope.nothing is not a registered plugin"

  Scenario: An integrations.json that is not JSON is an error
    Given a file "integrations.json" holding "{"
    When I run "integrations scan ."
    Then the exit code is 2
    And stderr mentions "it is not JSON"

  Scenario: A path that is not a directory is an error
    When I run "integrations scan missing"
    Then the exit code is 2
    And stderr mentions "is not a directory"

  Scenario: Suggest drafts a file from what is there
    Given a file "a.py" holding "LINEAR_API_KEY"
    And a file "b.py" holding "get('https://api.newvendor.io')"
    When I run "integrations scan --suggest ."
    Then it succeeds
    And it prints ""plugin": "linear.sync""
    And stderr mentions "place: b.py:1: host api.newvendor.io is not registered"

  Scenario: Suggest names a signature several plugins share
    Given a file "a.py" holding "https://api.github.com/x"
    When I run "integrations scan --suggest ."
    Then it succeeds
    And stderr mentions "choose: a.py:1 api.github.com:"

  Scenario: A script posts to Slack without its token on the command line
    Given the environment does not set "SLACK_TOKEN"
    When I run "integrations slack post --channel U1 --text hi"
    Then the exit code is 2
    And stderr mentions "SLACK_TOKEN is not set"
