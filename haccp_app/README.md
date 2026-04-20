# HACCP App

A Streamlit web application for HACCP (Hazard Analysis Critical Control Points) compliance tracking in hotel/hospitality settings.

## Features

- Kitchen temperature logging (fridge/freezer)
- Goods receipt tracking
- Open product management with expiry alerts
- Cleaning task documentation
- Housekeeping records
- Hotel guest management
- Role-based access control (admin, manager, kitchen_staff, housekeeping, staff)
- PDF report generation

## Prerequisites

- Python 3.11+
- PostgreSQL 14+ (or Podman/Docker to run it)

## Quick Start (Local Development)

1. Clone and install dependencies:
   ```bash
   cd haccp_app
   pip install -r requirements.txt
   ```

2. Start PostgreSQL (using Podman):
   ```bash
   podman run -d --name haccp-postgres \
     -e POSTGRES_PASSWORD=postgres \
     -e POSTGRES_DB=haccp \
     -p 5432:5432 \
     postgres:16
   ```

3. Initialize the database:
   ```bash
   python scripts/init_db.py --reset --seed-sample
   ```

4. Run the application:
   ```bash
   streamlit run main.py
   ```

5. Open http://localhost:8501 in your browser

## Default Credentials

- **Username:** admin
- **Password:** admin

Change the password after first login.

## Configuration

### Environment Variables (Container/Production)

Environment variables take priority and are used for container deployments:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_HOST` | PostgreSQL host | `localhost` |
| `DATABASE_PORT` | PostgreSQL port | `5432` |
| `DATABASE_NAME` | Database name | `haccp` |
| `DATABASE_USER` | Database user | `postgres` |
| `DATABASE_PASSWORD` | Database password | `postgres` |
| `ADMIN_USERNAME` | Initial admin username | `admin` |
| `ADMIN_PASSWORD` | Initial admin password | `admin` |

### Configuration File (Local Development)

For local development, settings are in `haccp.ini`:

```ini
[APP]
log_file = ./haccp.log
log_level_console = INFO
log_level_file = DEBUG

[DATABASE]
host = localhost
port = 5432
database = haccp
user = postgres
password = postgres

[HACCP]
fridge_temp_min = 0.0
fridge_temp_max = 7.0
freezer_temp_min = -25.0
freezer_temp_max = -18.0
expiry_warn_days = 3

[AUTH]
session_timeout_hours = 8
max_login_attempts = 5
lockout_minutes = 15
cookie_duration_days = 7
```

## Database Initialization

```bash
# Initialize with default settings
python scripts/init_db.py

# Reset database (drop all tables and recreate)
python scripts/init_db.py --reset

# Add sample test data
python scripts/init_db.py --seed-sample

# Custom database connection
python scripts/init_db.py --host localhost --port 5432 --database haccp --user postgres --password postgres
```

## Container Deployment

### Build Image

```bash
podman build -t haccp-app:latest -f deployment/Dockerfile .
```

### Run with Podman Compose

```bash
cp .env.example .env
# Edit .env with your settings
podman-compose up -d
```

### Kubernetes Deployment

Kubernetes manifests are in the `deployment/` directory, prefixed for apply order:

```bash
# Apply all manifests
kubectl apply -f deployment/

# Verify deployment
kubectl get pods -n haccp
```

See `deployment/README.md` for detailed Kubernetes deployment instructions.

## Project Structure

```
haccp_app/
├── main.py             # Application entry point
├── config.py           # Cleaning schedules, thresholds
├── haccp.ini           # Configuration file
├── db/                 # Database layer (PostgreSQL)
├── auth/               # Authentication & authorization
├── services/           # Business logic (alerts, reports)
├── ui/                 # Streamlit UI components and pages
├── utils/              # Logging, config utilities
├── scripts/            # CLI tools (init_db.py)
└── deployment/         # Kubernetes manifests, Dockerfile
```

## License

Internal use only.
