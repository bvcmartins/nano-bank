-- Nano Bank Core Database Schema
-- Part 3: Account Management Tables

-- Core account information
CREATE TABLE accounts (
    account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(customer_id) ON DELETE RESTRICT,
    account_number VARCHAR(12) UNIQUE NOT NULL, -- Canadian bank account format
    account_type account_type NOT NULL,
    currency VARCHAR(3) DEFAULT 'CAD' NOT NULL,
    balance DECIMAL(15,2) DEFAULT 0.00 NOT NULL,
    available_balance DECIMAL(15,2) DEFAULT 0.00 NOT NULL,
    status account_status DEFAULT 'pending_activation' NOT NULL,
    interest_rate DECIMAL(5,4) DEFAULT 0.0000,
    overdraft_limit DECIMAL(15,2) DEFAULT 0.00,
    minimum_balance DECIMAL(15,2) DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    activated_at TIMESTAMP WITH TIME ZONE,
    closed_at TIMESTAMP WITH TIME ZONE,

    -- Constraints
    CONSTRAINT chk_balance_precision CHECK (balance = ROUND(balance, 2)),
    CONSTRAINT chk_available_balance_precision CHECK (available_balance = ROUND(available_balance, 2)),
    CONSTRAINT chk_account_number_format CHECK (account_number ~ '^[0-9]{12}$'),
    CONSTRAINT chk_currency_cad CHECK (currency = 'CAD'),
    CONSTRAINT chk_interest_rate CHECK (interest_rate >= 0 AND interest_rate <= 1),
    CONSTRAINT chk_overdraft_limit CHECK (overdraft_limit >= 0),
    CONSTRAINT chk_minimum_balance CHECK (minimum_balance >= 0),
    CONSTRAINT chk_available_balance_logical CHECK (available_balance <= balance + overdraft_limit)
);

-- Account limits and restrictions
CREATE TABLE account_limits (
    limit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    daily_withdrawal_limit DECIMAL(15,2) DEFAULT 1000.00 NOT NULL,
    daily_transfer_limit DECIMAL(15,2) DEFAULT 5000.00 NOT NULL,
    monthly_transfer_limit DECIMAL(15,2) DEFAULT 50000.00 NOT NULL,
    annual_transfer_limit DECIMAL(15,2) DEFAULT 500000.00 NOT NULL,
    daily_withdrawal_used DECIMAL(15,2) DEFAULT 0.00 NOT NULL,
    daily_transfer_used DECIMAL(15,2) DEFAULT 0.00 NOT NULL,
    monthly_transfer_used DECIMAL(15,2) DEFAULT 0.00 NOT NULL,
    annual_transfer_used DECIMAL(15,2) DEFAULT 0.00 NOT NULL,
    last_reset_date DATE DEFAULT CURRENT_DATE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,

    -- Constraints
    CONSTRAINT chk_limits_positive CHECK (
        daily_withdrawal_limit > 0 AND
        daily_transfer_limit > 0 AND
        monthly_transfer_limit > 0 AND
        annual_transfer_limit > 0
    ),
    CONSTRAINT chk_used_within_limits CHECK (
        daily_withdrawal_used >= 0 AND daily_withdrawal_used <= daily_withdrawal_limit AND
        daily_transfer_used >= 0 AND daily_transfer_used <= daily_transfer_limit AND
        monthly_transfer_used >= 0 AND monthly_transfer_used <= monthly_transfer_limit AND
        annual_transfer_used >= 0 AND annual_transfer_used <= annual_transfer_limit
    ),
    CONSTRAINT chk_limit_hierarchy CHECK (
        daily_transfer_limit <= monthly_transfer_limit AND
        monthly_transfer_limit <= annual_transfer_limit
    )
);

-- Account holds (for pending transactions)
CREATE TABLE account_holds (
    hold_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    amount DECIMAL(15,2) NOT NULL,
    reason VARCHAR(255) NOT NULL,
    reference_id VARCHAR(100), -- External reference (transaction ID, etc.)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    released_at TIMESTAMP WITH TIME ZONE,

    -- Constraints
    CONSTRAINT chk_hold_amount_positive CHECK (amount > 0),
    CONSTRAINT chk_hold_expiry CHECK (expires_at > created_at)
);

-- Indexes for accounts table
CREATE INDEX idx_accounts_customer_id ON accounts(customer_id);
CREATE INDEX idx_accounts_number ON accounts(account_number);
CREATE INDEX idx_accounts_type ON accounts(account_type);
CREATE INDEX idx_accounts_status ON accounts(status);
CREATE INDEX idx_accounts_created_at ON accounts(created_at);

-- Indexes for account_limits table
CREATE UNIQUE INDEX idx_account_limits_account_id ON account_limits(account_id);
CREATE INDEX idx_account_limits_reset_date ON account_limits(last_reset_date);

-- Indexes for account_holds table
CREATE INDEX idx_account_holds_account_id ON account_holds(account_id);
CREATE INDEX idx_account_holds_expires_at ON account_holds(expires_at);
CREATE INDEX idx_account_holds_reference ON account_holds(reference_id);
CREATE INDEX idx_account_holds_active ON account_holds(account_id, expires_at) WHERE released_at IS NULL;