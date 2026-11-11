# Changelog

Notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

First release.

### Added

- Replay of Flyway, numbered or timestamped SQL files (including Liquibase formatted SQL), Supabase, Prisma Migrate, Drizzle Kit and git-tracked schema files in a throwaway PostgreSQL, MySQL, MariaDB or Oracle container.
- One self-contained HTML page per history: version timeline, per-step change review, ER diagram, table and column lineage that follows renames, range compare, global search and full DDL at any version.
- Renames marked confirmed or suspected; whole-table drops labelled apart from column drops.
- Pre-deploy check queries and per-engine locking hints in each step's review.
- Format chosen from the directory when `--format` is omitted; Prisma, Drizzle and Supabase histories found below the repo root in monorepos.
- `.up.sql`/`.down.sql` pairs (golang-migrate) in `sqlfiles`: only `.up.sql` is replayed.
- Flyway baseline migrations (`B…`): the highest one is applied first and older versions are skipped.
- `--placeholder name=value` and `--replace 'text=value'` substitutions in step SQL.
- A failed step's error suggests the next step: an image with the missing extension, or the substitution option.
- Two-DDL compare (`scripts/compare.sh`).
- CI report (`scripts/report.mjs`) with Markdown summary and `--fail-on` exit codes.
- ERD export: Mermaid from the command line (`scripts/export_erd.mjs`), Mermaid, SVG and PNG from the page.
- Headless self-check of the generated page (`scripts/check_html.mjs`).
- English and Korean output (`--lang`, `SCHEMA_TIMELINE_LANG`).
- Claude Code plugin and marketplace manifests.
