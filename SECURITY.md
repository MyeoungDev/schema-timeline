# Security

schema-timeline replays migration SQL inside a throwaway Docker container. It never connects to your own databases.

## What runs where

- Each replay starts a fresh database container from a public image, with networking disabled (`--network none`). Oracle needs a private IP, so it gets a Docker network created with `--internal` (no route outside), removed after the run.
- Migration files are applied as they are. A migration can run arbitrary SQL inside that container, so treat untrusted migrations (for example pull requests from forks) like untrusted code: run them on an isolated CI runner.
- The generated HTML page embeds every step's SQL text unless you pass `--no-sql`. Passwords in `IDENTIFIED BY` / `PASSWORD` clauses are masked; other data in the SQL (seed rows, comments) is not.
- Connection settings in project files (for example `flyway.conf`, `application.yml`) are only scanned to tell the database type and schema name. They are never used to connect, never written to the output and never sent anywhere.

## Reporting a vulnerability

Please open a private security advisory on the repository (GitHub → Security → Report a vulnerability) instead of a public issue. Include the migration files or the smallest history that reproduces the problem.
