Feature: Comparing a repository's calls with its integrations.json

  Scenario: Everything used is declared
    Given a repository with "tools/post.py" containing "requests.post('https://slack.com/api/x')"
    And its integrations.json declares "slack.post" in "tools/post.py"
    When I scan it
    Then it succeeds
    And it prints "all accounted for"

  Scenario: A signature hit that is not declared fails, naming the file and line
    Given a repository with "tools/post.py" containing "url = 'https://api.linear.app/graphql'"
    And its integrations.json declares nothing
    When I scan it
    Then it fails
    And it prints "tools/post.py:1: undeclared: api.linear.app (linear.sync)"

  Scenario: A use declared for another file does not cover this one
    Given a repository with "tools/a.py" containing "LINEAR_API_KEY"
    And the repository has "tools/b.py" containing "LINEAR_API_KEY"
    And its integrations.json declares "linear.sync" in "tools/a.py"
    When I scan it
    Then it fails
    And it prints "tools/b.py:1: undeclared: LINEAR_API_KEY"

  Scenario: A declared use with no hit is stale
    Given a repository with "tools/a.py" containing "print('hello')"
    And its integrations.json declares "linear.sync" in "tools/a.py"
    When I scan it
    Then it fails
    And it prints "stale: linear.sync: nothing in tools/a.py uses it"

  Scenario: One stale path among good ones is reported
    Given a repository with "tools/a.py" containing "LINEAR_API_KEY"
    And the repository has "tools/b.py" containing "print('hello')"
    And its integrations.json declares "linear.sync" in "tools/a.py,tools/b.py"
    When I scan it
    Then it fails
    And it prints "stale: linear.sync: nothing in tools/b.py uses it"

  Scenario: A plugin with signatures must name where it is used
    Given a repository with "a.py" containing "print('hello')"
    And its integrations.json declares "linear.sync" with no paths
    When I scan it
    Then it fails
    And it prints "stale: linear.sync: it names no paths"

  Scenario: An advisory use with no hit warns and passes
    Given a repository with "a.py" containing "print('hello')"
    And its integrations.json declares "linear.sync" in "a.py" as advisory
    When I scan it
    Then it succeeds
    And it prints "advisory: stale: linear.sync: nothing in a.py uses it"
    And it prints "with 1 advisory findings"

  Scenario: An advisory use may name no paths yet
    Given a repository with "a.py" containing "print('hello')"
    And its integrations.json declares "linear.sync" as advisory with no paths
    When I scan it
    Then it succeeds
    And it prints "advisory: stale: linear.sync: it names no paths"

  Scenario: A hit outside an advisory use's paths still fails
    Given a repository with "a.py" containing "LINEAR_API_KEY"
    And the repository has "b.py" containing "LINEAR_API_KEY"
    And its integrations.json declares "linear.sync" in "a.py" as advisory
    When I scan it
    Then it fails
    And it prints "b.py:1: undeclared: LINEAR_API_KEY"

  Scenario: A required use beside an advisory one still fails
    Given a repository with "a.py" containing "print('hello')"
    And its integrations.json declares "linear.sync" in "a.py" as advisory
    And its integrations.json declares "anthropic.api" in "a.py"
    When I scan it
    Then it fails
    And it prints "stale: anthropic.api: nothing in a.py uses it"

  Scenario: An advisory finding is an annotation in CI
    Given a repository with "a.py" containing "print('hello')"
    And its integrations.json declares "linear.sync" in "a.py" as advisory
    When I scan it in GitHub Actions
    Then it succeeds
    And it prints "::warning file=integrations.json::"

  Scenario: A Terraform stack can be advisory
    Given a repository with "a.py" containing "print('hello')"
    And its integrations.json has an advisory stack "infra" with "terraform.hetzner"
    When I scan it
    Then it succeeds
    And it prints "advisory: stale: terraform.hetzner"

  Scenario: A signature several plugins share is declared by any of them
    Given a repository with "lib/pr.py" containing "https://api.github.com/repos"
    And its integrations.json declares "github.edit-pr" in "lib/"
    When I scan it
    Then it succeeds

  Scenario: A manual plugin needs no hit
    Given a repository with "a.py" containing "print('hello')"
    And its integrations.json declares "inwx.registrar" in "help/dns.md"
    When I scan it
    Then it succeeds

  Scenario: A comment is not a call
    Given a repository with "a.py" containing "# see https://api.linear.app/graphql"
    And its integrations.json declares nothing
    When I scan it
    Then it succeeds

  Scenario: A host no plugin names is an unregistered provider
    Given a repository with "a.py" containing "get('https://api.newvendor.io/v1')"
    And its integrations.json declares nothing
    When I scan it
    Then it fails
    And it prints "a.py:1: host api.newvendor.io is not registered"

  Scenario: A host may be listed with its reason
    Given a repository with "a.py" containing "get('https://api.newvendor.io/v1')"
    And its integrations.json lists "other_hosts" entry "host" "api.newvendor.io"
    When I scan it
    Then it succeeds

  Scenario: The repository's own domains are not providers
    Given a repository with "a.py" containing "get('https://app.mine.example/x')"
    And its integrations.json owns "mine.example"
    When I scan it
    Then it succeeds

  Scenario: A name that merely ends like a registered host is not that host
    Given a repository with "a.py" containing "get('https://notslack.com/x')"
    And its integrations.json declares nothing
    When I scan it
    Then it fails
    And it prints "host notslack.com is not registered"

  Scenario: A workflow secret no plugin names fails
    Given a repository with ".github/workflows/a.yml" containing "token: ${{ secrets.NEWVENDOR_TOKEN }}"
    And its integrations.json declares nothing
    When I scan it
    Then it fails
    And it prints "secret NEWVENDOR_TOKEN is not registered"

  Scenario: A secret may be listed with its reason
    Given a repository with ".github/workflows/a.yml" containing "token: ${{ secrets.NEWVENDOR_TOKEN }}"
    And its integrations.json lists "other_secrets" entry "name" "NEWVENDOR_TOKEN"
    When I scan it
    Then it succeeds

  Scenario: A Terraform provider no plugin names fails
    Given a repository with "infra/versions.tf" containing "source = \"newvendor/thing\""
    And its integrations.json declares nothing
    When I scan it
    Then it fails
    And it prints "Terraform provider newvendor/thing is not registered"

  Scenario: A Terraform stack declares its providers for everything under it
    Given a repository with "infra/versions.tf" containing "source = \"hetznercloud/hcloud\""
    And its integrations.json has a stack "infra" with "terraform.hetzner"
    When I scan it
    Then it succeeds

  Scenario: A Terraform provider's source is matched whatever its case
    Given a repository with "infra/versions.tf" containing "source = \"Valodim/desec\""
    And its integrations.json has a stack "infra" with "terraform.desec"
    When I scan it
    Then it succeeds

  Scenario: A GitHub Action is matched by its prefix
    Given a repository with ".github/workflows/a.yml" containing "- uses: google-github-actions/auth@abc"
    And its integrations.json declares "google.cloud" in ".github/workflows/**"
    When I scan it
    Then it succeeds

  Scenario: An SDK import is a use
    Given a repository with "draft.py" containing "    import anthropic"
    And its integrations.json declares nothing
    When I scan it
    Then it fails
    And it prints "draft.py:1: undeclared"

  Scenario: Using a client here is a use of its plugin
    Given a repository with "app.py" containing "from integrations.clients.github_edit_pr import Change"
    And its integrations.json declares "github.edit-pr" in "app.py"
    When I scan it
    Then it succeeds

  Scenario: A file's presence can be a use
    Given a repository with "with-secrets.json" containing "{}"
    And its integrations.json declares nothing
    When I scan it
    Then it fails
    And it prints "with-secrets.json:1: undeclared: with-secrets.json (bitwarden.secrets)"

  Scenario: An excluded path is not read
    Given a repository with "tests/fake.py" containing "https://api.linear.app/graphql"
    And its integrations.json lists "exclude" entry "path" "tests/**"
    When I scan it
    Then it succeeds

  Scenario: Prose and lock files are not read
    Given a repository with "README.md" containing "https://api.linear.app/graphql"
    And the repository has "package-lock.json" containing "https://api.linear.app/graphql"
    And its integrations.json declares nothing
    When I scan it
    Then it succeeds

  Scenario: A script without a suffix is read when it has a shebang
    Given a repository with "deploy" containing "#!/bin/sh\nbws run"
    And its integrations.json declares nothing
    When I scan it
    Then it fails
    And it prints "deploy:2: undeclared: bws (bitwarden.secrets)"

  Scenario: No integrations.json is an error, not a pass
    Given a repository with "a.py" containing "print('hello')"
    When I scan it
    Then the exit code is 2
    And stderr mentions "integrations.json is missing"

  Scenario: A plugin the registry does not have is an error
    Given a repository with "a.py" containing "print('hello')"
    And its integrations.json declares "nope.nothing" in "a.py"
    When I scan it
    Then the exit code is 2
    And stderr mentions "nope.nothing is not a registered plugin"

  Scenario: An integrations.json that is not JSON is an error
    Given a repository with "integrations.json" containing "{"
    When I scan it
    Then the exit code is 2
    And stderr mentions "it is not JSON"

  Scenario: A path that is not a directory is an error
    When I scan the path "missing"
    Then the exit code is 2
    And stderr mentions "is not a directory"

  Scenario: Suggest drafts a file from what is there
    Given a repository with "a.py" containing "LINEAR_API_KEY"
    And the repository has "b.py" containing "get('https://api.newvendor.io')"
    When I ask for a suggestion
    Then it succeeds
    And it prints ""plugin": "linear.sync""
    And stderr mentions "place: b.py:1: host api.newvendor.io is not registered"

  Scenario: Suggest names a signature several plugins share
    Given a repository with "a.py" containing "https://api.github.com/x"
    When I ask for a suggestion
    Then it succeeds
    And stderr mentions "choose: a.py:1 api.github.com:"
