# HACCP App - Implementation Plan

Integration of production-ready components from RPA-Automation project.

**Approach:** Clean slate - rebuild database schema with auth tables included from the start.

## Overview

| Phase | Focus | Components | Effort |
|-------|-------|------------|--------|
| 1 | Foundation | New unified schema, logging, config | Medium |
| 2 | Authentication | Security module, login flow, sessions | Medium |
| 3 | Authorization | Access control, role-based UI gating | Low |
| 4 | Data & Testing | Seed data, testing, cleanup | Low |

---

## Phase 1: Foundation

### 1.0 Unified Schema

**Create:** `db/schema.sql` with complete schema (see Phase 2 for full SQL)

**Update:** `db/database.py` to:
- Load schema from `schema.sql` file instead of inline SQL
- Add `_verify_tables()` check on startup
- Add logging for all operations

**Implementation steps:**
1. Create `db/schema.sql` with all tables (auth + HACCP + indexes)
2. Update `HACCPDatabase._init_schema()` to read and execute schema file
3. Add table verification after schema init
4. Delete old `haccp.db` file

---

### 1.1 Custom Logger

**Source:** `RPA-Automation-develop/src/custom_logger.py`

**What to integrate:**
- `configure_global_logger()` - File + console handlers with configurable levels
- User context injection - Prepends username/role to log messages
- Audit logging - Per-inspection audit trails

**Target structure:**
```
haccp_app/
└── utils/
    └── logger.py
```

**Implementation steps:**
1. Copy and adapt `custom_logger.py` to `utils/logger.py`
2. Remove case-specific audit logging (not needed initially)
3. Add `LOG_PATH`, `LOG_LEVEL_FILE`, `LOG_LEVEL_CONSOLE` to config
4. Initialize logger in `app.py` before other imports
5. Replace `print()` statements and add logging to:
   - Database operations (`db/database.py`)
   - Repository methods (`db/repositories.py`)
   - Alert service (`services/alerts.py`)
   - Report generation (`services/reports.py`)

**Example usage:**
```python
from utils.logger import get_logger
log = get_logger(__name__)

log.info("Temperature logged", extra={"location": "fridge", "temp": 4.2})
log.warning("Product expiring soon", extra={"product_id": 123})
```

---

### 1.2 Configuration Handler

**Source:** `RPA-Automation-develop/src/cls/config.py`

**What to integrate:**
- Singleton `ConfigHandler` class
- INI file-based configuration
- Auto-create defaults on first run

**Target structure:**
```
haccp_app/
├── config.py          # Keep existing (rename to config_constants.py)
├── utils/
│   └── config.py      # New ConfigHandler
└── haccp.ini          # Runtime config file
```

**Configuration sections:**
```ini
[APP]
db_path = ./haccp.db
log_path = ./logs/
log_level_console = INFO
log_level_file = DEBUG

[HACCP]
fridge_temp_min = 0.0
fridge_temp_max = 7.0
freezer_temp_min = -25.0
freezer_temp_max = -18.0
expiry_warn_days = 3

[CLEANING]
kaffeemaschine_frequency = 1
teestation_frequency = 1
buffet_frequency = 1
eierstation_frequency = 1

[AUTH]
session_timeout_hours = 8
max_login_attempts = 5
lockout_minutes = 15
```

**Implementation steps:**
1. Copy `config.py` to `utils/config.py`
2. Rename existing `config.py` to `config_constants.py` (static definitions)
3. Create `_create_default_config()` with HACCP-specific defaults
4. Update imports throughout app to use `ConfigHandler.get_instance()`
5. Move hardcoded values from `config_constants.py` to `haccp.ini`

---

## Phase 2: Authentication

### 2.1 Security Module

**Source:** `RPA-Automation-develop/src/workflow/security.py`

**What to integrate:**
- `hash_password(password, salt)` - PBKDF2-HMAC-SHA256
- `verify_password(password, stored_hash, salt)` - Password verification
- `check_login_attempts(ip, max_attempts, window)` - Brute-force protection
- `get_client_ip()` - IP extraction from Streamlit context

**Target structure:**
```
haccp_app/
└── auth/
    ├── __init__.py
    ├── security.py     # Password hashing, IP checks
    ├── session.py      # Session management
    └── models.py       # User, Session dataclasses
```

**Complete unified schema:** `db/schema.sql`

```sql
-- ============================================
-- AUTH TABLES
-- ============================================

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'staff',
    display_name TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_login TEXT
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_key TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    ip_address TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    username TEXT,
    attempted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    success INTEGER DEFAULT 0
);

-- ============================================
-- HACCP TABLES (with audit fields)
-- ============================================

CREATE TABLE kitchen_temperature (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    temperature REAL NOT NULL,
    employee TEXT NOT NULL,
    location TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kitchen_goods_receipt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL,
    amount TEXT NOT NULL,
    receipt_date TEXT NOT NULL,
    employee TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kitchen_open_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL,
    amount TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kitchen_cleaning (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station TEXT NOT NULL,
    tasks TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE housekeeping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datum TEXT NOT NULL,
    raum TEXT NOT NULL,
    aufgaben TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE housekeeping_basic_cleaning (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datum TEXT NOT NULL,
    abreise TEXT,
    bleibe TEXT,
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE hotel_guests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    anreise TEXT NOT NULL,
    abreise TEXT NOT NULL,
    hund_mit INTEGER NOT NULL,
    notizen TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE hotel_arrival_control (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datum TEXT NOT NULL,
    zimmer TEXT NOT NULL,
    employee TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- INDEXES
-- ============================================

CREATE INDEX idx_temp_timestamp ON kitchen_temperature(timestamp);
CREATE INDEX idx_temp_location ON kitchen_temperature(location);
CREATE INDEX idx_products_expiry ON kitchen_open_products(expiry_date);
CREATE INDEX idx_cleaning_station ON kitchen_cleaning(station);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_key ON sessions(session_key);
CREATE INDEX idx_login_ip ON login_attempts(ip_address);
```

**Implementation steps:**
1. Create `auth/` module structure
2. Copy security functions from RPA project
3. Add user/session tables to `db/database.py` schema
4. Create `UserRepository` and `SessionRepository`
5. Implement login flow:
   - Check IP not locked out
   - Verify credentials
   - Create session
   - Store in `st.session_state`

---

### 2.2 User Management

**New file:** `auth/users.py`

**Features:**
- Create/update/deactivate users
- Password reset
- List users (admin only)

**UI additions:**
```
haccp_app/
└── ui/
    └── pages/
        └── admin.py    # User management page (admin only)
```

**Implementation steps:**
1. Create `User` dataclass in `auth/models.py`
2. Create `UserRepository` with CRUD operations
3. Add admin page for user management
4. Add "Admin" option to navigation (role-gated)

---

## Phase 3: Authorization

### 3.1 Access Control

**Source:** `RPA-Automation-develop/src/cls/accesscontrol.py`

**Role definitions for HACCP:**
```python
ROLES = {
    'admin': {
        'description': 'Full system access',
        'features': {'user_management', 'settings', 'all_reports', 'all_pages'}
    },
    'manager': {
        'description': 'Hotel manager - all HACCP functions',
        'features': {'kitchen', 'housekeeping', 'hotel', 'reports', 'view_alerts'}
    },
    'kitchen_staff': {
        'description': 'Kitchen workers',
        'features': {'kitchen', 'view_alerts'}
    },
    'housekeeping': {
        'description': 'Housekeeping staff',
        'features': {'housekeeping'}
    }
}
```

**Target structure:**
```
haccp_app/
└── auth/
    └── access.py       # Role definitions, permission checks
```

**Implementation steps:**
1. Create `access.py` with role definitions
2. Add `@require_role(roles)` decorator for page functions
3. Add `@require_feature(feature)` decorator for specific actions
4. Gate navigation options based on user role
5. Filter data based on permissions (if multi-facility later)

**Example usage:**
```python
from auth.access import require_role, require_feature

@require_role(['admin', 'manager', 'kitchen_staff'])
def render_kitchen_page(db, cleaning_schedule):
    ...

@require_feature('reports')
def generate_haccp_report(db, start, end):
    ...
```

---

### 3.2 Login UI

**New file:** `ui/pages/login.py`

**Features:**
- Username/password form
- Error messages for invalid credentials
- Lockout message if IP blocked
- "Remember me" option (longer session)

**App flow changes:**
```python
# app.py
def main():
    if not is_authenticated():
        render_login_page()
        return

    # ... rest of app
```

**Implementation steps:**
1. Create `login.py` with login form
2. Add `is_authenticated()` check using session state
3. Add logout button to sidebar
4. Redirect to login on session expiry

---

## Phase 4: Seed Data & Cleanup

### 4.1 Database Initialization Script

**Source:** `RPA-Automation-develop/db_init.py`

**What to create:**
- CLI script to initialize fresh database
- Create default admin user
- Optionally seed sample data for testing

**New file:** `scripts/init_db.py`

```python
# Usage:
# python scripts/init_db.py                    # Fresh DB with admin user
# python scripts/init_db.py --seed-sample      # Include sample HACCP data
# python scripts/init_db.py --reset            # Drop and recreate
```

**Implementation steps:**
1. Create `scripts/init_db.py` with CLI args
2. Create default admin (admin/admin - force change on first login)
3. Optional sample data: test temperatures, products, cleaning records

---

### 4.2 Cleanup

**Remove:**
- `db_init.py` (old legacy file - already removed)
- `RPA-Automation-develop/` folder (after extracting needed code)
- Any `.pyc` / `__pycache__` artifacts

**Verify:**
- All imports work
- App starts fresh with new schema
- Login flow works end-to-end

---

## File Changes Summary

### New Files
```
haccp_app/
├── utils/
│   ├── __init__.py
│   ├── logger.py           # Custom logging
│   └── config_handler.py   # ConfigHandler singleton
├── auth/
│   ├── __init__.py
│   ├── security.py         # Password hashing, IP checks
│   ├── session.py          # Session management
│   ├── access.py           # Role-based access control
│   └── models.py           # User, Session dataclasses
├── ui/pages/
│   ├── login.py            # Login page
│   └── admin.py            # User management (admin)
├── db/
│   └── schema.sql          # Complete unified schema
├── scripts/
│   └── init_db.py          # Database initialization CLI
└── haccp.ini               # Runtime configuration
```

### Modified Files
```
haccp_app/
├── app.py                  # Add auth check, logout button
├── config.py               # Keep for constants, add role definitions
├── db/
│   ├── database.py         # Load schema from schema.sql, add logging
│   ├── models.py           # Add User, Session models + audit fields
│   └── repositories.py     # Add UserRepo, SessionRepo, LoginAttemptRepo
└── requirements.txt        # No new deps needed
```

### Files to Delete
```
haccp_app/
├── haccp.db                # Will be recreated
└── RPA-Automation-develop/ # After extracting needed code
```

---

## Implementation Order

```
Phase 1: Foundation
├── Create db/schema.sql (unified schema)
├── Update db/database.py (load from file, verify tables)
├── Create utils/logger.py
├── Create utils/config_handler.py
└── Create haccp.ini with defaults

Phase 2: Authentication
├── Create auth/security.py (hashing, IP checks)
├── Create auth/models.py (User, Session)
├── Create auth/session.py (session management)
├── Add UserRepo, SessionRepo, LoginAttemptRepo
└── Wire up login flow in app.py

Phase 3: Authorization
├── Create auth/access.py (roles, decorators)
├── Create ui/pages/login.py
├── Create ui/pages/admin.py
├── Gate navigation by role
└── Add logout to sidebar

Phase 4: Seed & Cleanup
├── Create scripts/init_db.py
├── Test full flow
└── Remove RPA-Automation-develop/
```

---

## Testing Checklist

### Phase 1
- [ ] Fresh `haccp.db` created with all tables
- [ ] All tables verified on startup
- [ ] Logs written to file and console
- [ ] Log levels configurable via `haccp.ini`
- [ ] Config values loaded from `haccp.ini`
- [ ] Default config created on first run

### Phase 2
- [ ] Password hashed with PBKDF2
- [ ] Login with valid credentials succeeds
- [ ] Login with invalid credentials fails
- [ ] IP locked after 5 failed attempts
- [ ] Session expires after configured timeout

### Phase 3
- [ ] Admin can access all pages
- [ ] Kitchen staff can only access kitchen page
- [ ] Unauthenticated users redirected to login
- [ ] Logout clears session

### Phase 4
- [ ] `init_db.py` creates fresh database
- [ ] Default admin user created
- [ ] Sample data seeded with `--seed-sample` flag
- [ ] RPA-Automation folder removed

---

## Notes

- **No new dependencies required** - All security uses Python stdlib (`hashlib`, `secrets`)
- **Clean slate** - Delete `haccp.db` and run `scripts/init_db.py` to create fresh
- **Audit fields included** - All HACCP tables have `created_by` and `created_at` from the start
- **Multi-facility ready** - Access control can be extended to per-facility permissions later
