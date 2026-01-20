#!/usr/bin/env python3
"""
Database initialization script for HACCP application.
Creates database schema and default admin user.

Usage:
    python scripts/init_db.py           # Initialize if not exists
    python scripts/init_db.py --reset   # Drop and recreate all tables
    python scripts/init_db.py --seed-sample  # Add sample test data
"""
import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import HACCPDatabase, UserRepository
from auth import hash_password

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def reset_database(db: HACCPDatabase):
    """Drop all tables in the database."""
    logger.warning("Dropping all tables in database...")

    tables = [
        "hotel_arrival_control",
        "hotel_guests",
        "housekeeping_basic_cleaning",
        "housekeeping",
        "kitchen_cleaning",
        "kitchen_open_products",
        "kitchen_goods_receipt",
        "kitchen_temperature",
        "sessions",
        "login_attempts",
        "users",
    ]

    for table in tables:
        try:
            db.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            logger.info(f"Dropped table: {table}")
        except Exception as e:
            logger.warning(f"Could not drop table {table}: {e}")


def create_default_admin(db: HACCPDatabase):
    """Create default admin user if not exists."""
    user_repo = UserRepository(db)

    # Check if admin already exists
    existing = user_repo.get_by_username("admin")
    if existing:
        logger.info("Admin user already exists")
        return

    # Create admin user with password 'admin'
    password_hash, password_salt = hash_password("admin")

    from auth.models import User

    admin = User(
        username="admin",
        password_hash=password_hash,
        password_salt=password_salt,
        role="admin",
        display_name="Administrator",
        active=True,
    )

    user_id = user_repo.insert(admin)
    logger.info(f"Created admin user (id={user_id}) with password 'admin'")
    logger.warning("IMPORTANT: Change the default admin password after first login!")


def seed_sample_data(db: HACCPDatabase):
    """Add sample data for testing."""
    from db import (
        KitchenTemperatureRepo,
        GoodsReceiptRepo,
        OpenProductRepo,
        HotelGuestRepo,
    )
    from db.models import KitchenTemperature, GoodsReceipt, OpenProduct, HotelGuest

    logger.info("Seeding sample data...")

    # Sample temperatures
    temp_repo = KitchenTemperatureRepo(db)
    temps = [
        KitchenTemperature(location="fridge", temperature=4.5, employee="Max"),
        KitchenTemperature(location="fridge", temperature=5.0, employee="Anna"),
        KitchenTemperature(location="freezer", temperature=-20.0, employee="Max"),
        KitchenTemperature(location="freezer", temperature=-19.5, employee="Anna"),
    ]
    for t in temps:
        temp_repo.insert(t)
    logger.info(f"Added {len(temps)} temperature records")

    # Sample goods receipts
    goods_repo = GoodsReceiptRepo(db)
    goods = [
        GoodsReceipt(
            product="Milch",
            amount="10 Liter",
            receipt_date=date.today().isoformat(),
            employee="Max",
        ),
        GoodsReceipt(
            product="Butter",
            amount="5 kg",
            receipt_date=date.today().isoformat(),
            employee="Anna",
        ),
    ]
    for g in goods:
        goods_repo.insert(g)
    logger.info(f"Added {len(goods)} goods receipt records")

    # Sample open products (some expiring soon)
    products_repo = OpenProductRepo(db)
    products = [
        OpenProduct(
            product="Offene Milch",
            amount="2 Liter",
            expiry_date=(date.today() + timedelta(days=2)).isoformat(),
        ),
        OpenProduct(
            product="Aufschnitt",
            amount="500g",
            expiry_date=(date.today() + timedelta(days=1)).isoformat(),
        ),
        OpenProduct(
            product="Joghurt",
            amount="1 kg",
            expiry_date=(date.today() + timedelta(days=5)).isoformat(),
        ),
    ]
    for p in products:
        products_repo.insert(p)
    logger.info(f"Added {len(products)} open product records")

    # Sample guests
    guest_repo = HotelGuestRepo(db)
    guests = [
        HotelGuest(
            name="Familie Müller",
            anreise=date.today().isoformat(),
            abreise=(date.today() + timedelta(days=3)).isoformat(),
            hund_mit=True,
            notizen="Zimmer 101, Hund: Rex",
        ),
        HotelGuest(
            name="Herr Schmidt",
            anreise=(date.today() - timedelta(days=1)).isoformat(),
            abreise=(date.today() + timedelta(days=2)).isoformat(),
            hund_mit=False,
            notizen="Zimmer 205",
        ),
    ]
    for guest in guests:
        guest_repo.insert(guest)
    logger.info(f"Added {len(guests)} guest records")

    logger.info("Sample data seeding complete")


def main():
    parser = argparse.ArgumentParser(
        description="Initialize HACCP application database"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all database tables",
    )
    parser.add_argument(
        "--seed-sample",
        action="store_true",
        help="Add sample test data",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="PostgreSQL host (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5432,
        help="PostgreSQL port (default: 5432)",
    )
    parser.add_argument(
        "--database",
        type=str,
        default="haccp",
        help="Database name (default: haccp)",
    )
    parser.add_argument(
        "--user",
        type=str,
        default="postgres",
        help="Database user (default: postgres)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default="postgres",
        help="Database password (default: postgres)",
    )

    args = parser.parse_args()

    logger.info(f"Connecting to PostgreSQL: {args.database}@{args.host}:{args.port}")

    # Reset if requested (before connecting with schema init)
    if args.reset:
        # Connect without schema init to drop tables
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=args.host,
                port=args.port,
                database=args.database,
                user=args.user,
                password=args.password,
            )
            conn.autocommit = True
            cursor = conn.cursor()

            tables = [
                "hotel_arrival_control",
                "hotel_guests",
                "housekeeping_basic_cleaning",
                "housekeeping",
                "kitchen_cleaning",
                "kitchen_open_products",
                "kitchen_goods_receipt",
                "kitchen_temperature",
                "sessions",
                "login_attempts",
                "users",
            ]

            for table in tables:
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
                    logger.info(f"Dropped table: {table}")
                except Exception as e:
                    logger.warning(f"Could not drop table {table}: {e}")

            cursor.close()
            conn.close()
            logger.info("Tables dropped successfully")
        except Exception as e:
            logger.error(f"Error dropping tables: {e}")
            sys.exit(1)

        # Reset singleton so we get fresh connection
        HACCPDatabase.reset()

    # Initialize database (schema is auto-created)
    try:
        db = HACCPDatabase.get_instance(
            host=args.host,
            port=args.port,
            database=args.database,
            user=args.user,
            password=args.password,
        )
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        sys.exit(1)

    # Create default admin
    create_default_admin(db)

    # Seed sample data if requested
    if args.seed_sample:
        seed_sample_data(db)

    logger.info("Database initialization complete")


if __name__ == "__main__":
    main()
