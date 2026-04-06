# Nano Bank Core Database Schema

This directory contains the PostgreSQL DDL scripts for the core banking system tables.

## Overview

The schema implements a complete challenger bank system with:
- Double-entry bookkeeping for transaction integrity
- Canadian banking standards (CAD currency, postal codes, SIN)
- Comprehensive audit trails and security monitoring
- KYC/AML compliance features
- Real-time fraud detection capabilities

## File Structure

Execute the scripts in the following order:

1. **00_init.sql** - Database initialization and extensions
2. **01_enums.sql** - Enum type definitions
3. **02_customers.sql** - Customer and identity management
4. **03_accounts.sql** - Account management and limits
5. **04_transactions.sql** - Transaction processing (double-entry)
6. **05_security.sql** - Security and compliance monitoring
7. **06_triggers.sql** - Database triggers and functions

## Quick Setup

```bash
# Connect to PostgreSQL
psql -U postgres -d nano_bank

# Execute all scripts in order
\i 00_init.sql
\i 01_enums.sql
\i 02_customers.sql
\i 03_accounts.sql
\i 04_transactions.sql
\i 05_security.sql
\i 06_triggers.sql
```

## Key Features

### Financial Integrity
- **Double-entry bookkeeping** via `transaction_entries` table
- **Balance validation** triggers ensure accuracy
- **Transaction reversals** for error correction
- **Fee tracking** and management

### Canadian Compliance
- **SIN validation** for customer identification
- **Canadian postal code** format validation
- **CAD currency** as default
- **Provincial codes** validation

### Security & Monitoring
- **Comprehensive audit logs** for all table changes
- **Session management** with device fingerprinting
- **Failed login tracking** and suspicious activity monitoring
- **Configurable monitoring rules** with risk scoring

### Account Management
- **Flexible account types** (checking, savings)
- **Daily/monthly/annual limits** with automatic tracking
- **Account holds** for pending transactions
- **Interest rate support**

## Database Constraints

The schema includes extensive constraints to ensure data integrity:

- **Check constraints** for data validation
- **Foreign key constraints** for referential integrity
- **Unique constraints** for business rules
- **Trigger-based validation** for complex business logic

## Indexing Strategy

Indexes are optimized for:
- Customer lookups (email, phone, SIN)
- Account operations (account number, customer ID)
- Transaction processing (reference numbers, dates)
- Security monitoring (IP addresses, session tokens)
- Audit trail queries (entity types, timestamps)

## Sample Usage

After setup, you can create a customer and account:

```sql
-- Create a customer
INSERT INTO customers (email, phone_number, first_name, last_name, date_of_birth, sin)
VALUES ('john@example.com', '+1-416-555-0123', 'John', 'Doe', '1990-01-01', '123456789');

-- Account will be created automatically with generated account number
-- Transaction entries will maintain double-entry bookkeeping automatically
```