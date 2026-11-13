# DORA Maturity Reporting

An open-source, self-hosted project for understanding software delivery performance
and engineering maturity across repositories, projects and teams. Its focus is
holistic DORA reporting: delivery throughput, stability and the engineering
capabilities that support sustainable improvement, rather than a score for any
single platform.

The application provides a CLI, a FastAPI service and web dashboard, report
generation, optional PostgreSQL persistence and Grafana dashboard definitions.

## Reporting scope and current implementation

A useful DORA view connects production deployment frequency, change lead time,
change failure rate and failed-deployment recovery time with their supporting
evidence. Deployment rework and wider reliability context help explain those
outcomes. Quality assurance, pipeline automation, security practices and
collaboration provide complementary maturity signals; they do not replace delivery
outcomes or measure individual developer productivity.

The reporting scope is platform-independent. The currently implemented collector
is **Azure DevOps only**. Other source-control, deployment, incident-management and
observability integrations are not implemented. Describing the broader scope does
not imply that the application already collects or calculates every DORA metric.

The project is under development. Maturity scores are a configurable assessment
model, not a certification. Some Grafana panels use collection-run counts and
durations as delivery-performance proxies; they are not validated DORA measurements.
Use synthetic data and disposable credentials while evaluating the application.

## Available capabilities

- Collect repository, pull-request, build, test and branch-policy data from Azure DevOps.
- Assess quality assurance, deployment pipelines, security compliance and collaboration.
- Aggregate maturity by project and security-group segment.
- Export JSON and Excel reports and compare two sets of Excel exports.
- Optionally persist runs and metrics to PostgreSQL, with Alembic migrations.
- Browse collection runs through the API and bundled web UI.

## Quick start

Use Python 3.10 or newer. The Python namespace remains `devops_metrics`, and the
installed CLI remains `devops-metrics`.

```bash
git clone https://github.com/kamo62/dora-maturity-reporting.git
cd dora-maturity-reporting
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Set your own `ADO_AZURE_DEVOPS_PAT` and `ADO_AZURE_DEVOPS_ORG` in the ignored `.env`
file before collecting data. Use only organisations and repositories you are
authorised to access. Leave `DATABASE_URL` empty for file-only operation.

```bash
# Local dashboard and API, without a database
python -m uvicorn devops_metrics.api.main:app --host 127.0.0.1 --port 8000

# Collect reports for one authorised project
devops-metrics collect-raw --project "YOUR_PROJECT_NAME_HERE" --maturity --segments
```

Visit <http://localhost:8000/ui/> for the dashboard, `/docs` for the API reference,
and `/healthz` for health. Reports are written under the ignored `exports/` directory.

`--minimal` limits collection to ten projects; it is not an offline mode.
Use `devops-metrics collect-raw --help` for collection options.

## Optional database and Grafana

For the local Compose stack, supply fresh `POSTGRES_PASSWORD`, `GRAFANA_USER` and
`GRAFANA_PASSWORD` values in `.env`. Use a URL-safe database password.

```bash
docker compose up --build -d db api grafana
```

Compose binds the API, database and Grafana ports to loopback. The stack uses
TimescaleDB and requests local automatic migrations. Inspect the API logs before
assuming migrations have completed. It is not a production deployment setup.

For a database outside Compose, export your own `DATABASE_URL` in the shell before
running Alembic:

```bash
alembic current
alembic upgrade head
```

Migrations modify the configured database. Review the target and back up important
data first. Set `DISABLE_TIMESCALE_FEATURES=true` for PostgreSQL without TimescaleDB;
some Grafana queries still require adaptation. Collection persists to the database
only when requested, for example with `--write-db --db-strict`. Do not use
`--db-reset` unless you intend to delete stored reporting data.

## Configuration and data

- Documentation uses `YOUR_..._HERE` placeholders. They are not usable credentials.
- Automated tests use synthetic accounts, tokens, IDs, timestamps and metrics.
  Email examples use the reserved `example.com` domain. No collected reports are bundled.
- Grafana dashboard targets and cost assumptions are illustrative inputs, not measured
  business results. Replace them before interpreting the dashboards.
- There are no manual staff, executive-owner or project-assignment mappings.
  Segment assessment uses security-group entitlements from your configured source.
- No projects or metrics are exempted by default. Configure your own exemptions locally
  through `ADO_EXEMPTIONS_FILE` or the local UI.
- Environment-file loading is limited to `.env` in the working directory, without
  searching parent folders. Authentication uses the explicitly configured PAT,
  not inherited Azure Pipelines tokens.
- Known excluded company identifiers are rejected in collection targets and
  configured paths. These checks cannot identify private data hidden behind an
  unrelated alias or inside an otherwise innocuously named file. Use a fresh
  environment, a separate local database and only data you are authorised to use.
- The repository contains source, configuration and this README only. Generated
  reports, spreadsheets, presentations and additional documentation are excluded.
  JSON/Excel generation remains available when you explicitly run a collection.

The API has limited API-key controls, not a complete multi-user authentication
system. Keep it on loopback while evaluating it. Do not expose it publicly or
connect sensitive production data without reviewing access controls, exported-file
access and retention. Never commit completed environment files, exports or private
organisation data.

## Tests

```bash
python -m pytest tests/
```

Tests use mocks for remote services. Keep test credentials and records synthetic.
No live collection is needed to run the suite.

## Licence

[MIT](LICENSE). Copyright Kamogelo.
