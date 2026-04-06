-- Nano Bank Core Database Schema
-- Part 1: Enum Types

-- Customer KYC Status
CREATE TYPE kyc_status AS ENUM (
    'pending',
    'verified',
    'rejected',
    'under_review'
);

-- Address Types
CREATE TYPE address_type AS ENUM (
    'residential',
    'mailing',
    'business'
);

-- Account Types
CREATE TYPE account_type AS ENUM (
    'checking',
    'savings'
);

-- Account Status
CREATE TYPE account_status AS ENUM (
    'active',
    'frozen',
    'closed',
    'pending_activation'
);

-- Transaction Types
CREATE TYPE transaction_type AS ENUM (
    'debit',
    'credit'
);

-- Transaction Status
CREATE TYPE transaction_status AS ENUM (
    'pending',
    'completed',
    'failed',
    'reversed',
    'cancelled'
);

-- Entry Types for Double-Entry Bookkeeping
CREATE TYPE entry_type AS ENUM (
    'debit',
    'credit'
);

-- Document Types for KYC
CREATE TYPE document_type AS ENUM (
    'passport',
    'drivers_license',
    'health_card',
    'utility_bill',
    'bank_statement',
    'employment_letter'
);

-- Document Verification Status
CREATE TYPE verification_status AS ENUM (
    'pending',
    'verified',
    'rejected',
    'expired'
);

-- Audit Action Types
CREATE TYPE audit_action AS ENUM (
    'create',
    'update',
    'delete',
    'login',
    'logout',
    'transaction'
);