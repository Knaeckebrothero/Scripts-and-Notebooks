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

## Installation

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

Database and logging settings are in `haccp.ini`:

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
```

## Database Initialization Options

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
└── scripts/            # CLI tools
```

## License

Internal use only.
