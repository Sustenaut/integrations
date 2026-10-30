# Working in this repository

Managed by pubkit 0.23.2: `pubkit sync` rewrites this file and `pubkit
sync --check` fails when it differs, so every pub* repository gives agents
and people the same rules. Repository-specific guidance lives in the
README. The standard behind these rules is pubkit's README:
<https://github.com/Sustenaut/pubkit#readme>.

## Pull requests: ready, or draft

- Open a pull request **ready for review** by default. Merging is gated by
  the checks main's ruleset requires (`ci / passed`, and `release /
  release` where pubrel releases), and `ci / passed` fails when any CI job
  does, SonarQube Cloud's quality gate included. Draft adds no safety.
- Mark it **draft** only when something else must happen first: another
  pull request merged, a release published, a pin moved, a person's
  decision or action. Say in its description what it waits on, and mark it
  ready once that is done.
- A stacked pull request says so in its title and description.
