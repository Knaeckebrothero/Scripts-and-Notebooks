-- ============================================
-- HACCP App Database Schema (PostgreSQL)
-- Complete unified schema with auth + HACCP tables
-- ============================================

-- ============================================
-- AUTH TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'staff',
    display_name TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    session_key TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    ip_address TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id SERIAL PRIMARY KEY,
    ip_address TEXT NOT NULL,
    username TEXT,
    attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    success BOOLEAN DEFAULT FALSE
);

-- ============================================
-- HACCP TABLES (with audit fields)
-- ============================================

CREATE TABLE IF NOT EXISTS kitchen_temperature (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    temperature REAL NOT NULL,
    employee TEXT NOT NULL,
    location TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kitchen_goods_receipt (
    id SERIAL PRIMARY KEY,
    product TEXT NOT NULL,
    amount TEXT NOT NULL,
    receipt_date DATE NOT NULL,
    employee TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kitchen_open_products (
    id SERIAL PRIMARY KEY,
    product TEXT NOT NULL,
    amount TEXT NOT NULL,
    expiry_date DATE NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kitchen_cleaning (
    id SERIAL PRIMARY KEY,
    station TEXT NOT NULL,
    tasks TEXT NOT NULL,
    completed_at TIMESTAMP NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS housekeeping (
    id SERIAL PRIMARY KEY,
    datum DATE NOT NULL,
    raum TEXT NOT NULL,
    aufgaben TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS housekeeping_basic_cleaning (
    id SERIAL PRIMARY KEY,
    datum DATE NOT NULL,
    abreise TEXT,
    bleibe TEXT,
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hotel_guests (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    anreise DATE NOT NULL,
    abreise DATE NOT NULL,
    hund_mit BOOLEAN NOT NULL,
    notizen TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hotel_arrival_control (
    id SERIAL PRIMARY KEY,
    datum DATE NOT NULL,
    zimmer TEXT NOT NULL,
    employee TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- INDEXES
-- ============================================

CREATE INDEX IF NOT EXISTS idx_temp_timestamp ON kitchen_temperature(timestamp);
CREATE INDEX IF NOT EXISTS idx_temp_location ON kitchen_temperature(location);
CREATE INDEX IF NOT EXISTS idx_products_expiry ON kitchen_open_products(expiry_date);
CREATE INDEX IF NOT EXISTS idx_cleaning_station ON kitchen_cleaning(station);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_key ON sessions(session_key);
CREATE INDEX IF NOT EXISTS idx_login_ip ON login_attempts(ip_address);
