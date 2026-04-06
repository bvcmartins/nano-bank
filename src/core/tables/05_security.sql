-- Nano Bank Core Database Schema
-- Part 5: Security and Compliance Tables

-- Comprehensive audit log
CREATE TABLE audit_logs (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type VARCHAR(100) NOT NULL, -- 'customer', 'account', 'transaction', etc.
    entity_id UUID NOT NULL,
    action audit_action NOT NULL,
    old_values JSONB,
    new_values JSONB,
    user_id UUID, -- Could be customer or internal user
    session_id VARCHAR(255),
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,

    -- Constraints
    CONSTRAINT chk_entity_id_not_null CHECK (entity_id IS NOT NULL)
);

-- Session management for security
CREATE TABLE user_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    session_token VARCHAR(255) UNIQUE NOT NULL,
    ip_address INET NOT NULL,
    user_agent TEXT,
    device_fingerprint VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    last_activity_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    terminated_at TIMESTAMP WITH TIME ZONE,
    termination_reason VARCHAR(100),

    -- Constraints
    CONSTRAINT chk_session_expiry CHECK (expires_at > created_at),
    CONSTRAINT chk_session_active_logic CHECK (
        (is_active = TRUE AND terminated_at IS NULL) OR
        (is_active = FALSE AND terminated_at IS NOT NULL)
    )
);

-- Failed login attempts tracking
CREATE TABLE failed_login_attempts (
    attempt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255),
    ip_address INET NOT NULL,
    user_agent TEXT,
    failure_reason VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,

    -- Constraints
    CONSTRAINT chk_email_or_customer CHECK (email IS NOT NULL)
);

-- Suspicious activity monitoring
CREATE TABLE suspicious_activities (
    activity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(customer_id) ON DELETE CASCADE,
    account_id UUID REFERENCES accounts(account_id) ON DELETE CASCADE,
    activity_type VARCHAR(100) NOT NULL, -- 'unusual_login', 'large_transaction', 'velocity', etc.
    risk_score INTEGER NOT NULL, -- 1-100 scale
    description TEXT NOT NULL,
    metadata JSONB,
    status VARCHAR(50) DEFAULT 'open' NOT NULL, -- 'open', 'investigating', 'resolved', 'false_positive'
    assigned_to VARCHAR(255), -- Internal user handling the case
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    resolved_at TIMESTAMP WITH TIME ZONE,
    resolution_notes TEXT,

    -- Constraints
    CONSTRAINT chk_risk_score_range CHECK (risk_score >= 1 AND risk_score <= 100),
    CONSTRAINT chk_status_values CHECK (status IN ('open', 'investigating', 'resolved', 'false_positive')),
    CONSTRAINT chk_resolution_logic CHECK (
        (status = 'resolved' AND resolved_at IS NOT NULL) OR
        (status != 'resolved')
    )
);

-- Device fingerprinting and recognition
CREATE TABLE known_devices (
    device_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    device_fingerprint VARCHAR(255) UNIQUE NOT NULL,
    device_name VARCHAR(100),
    device_type VARCHAR(50), -- 'mobile', 'desktop', 'tablet'
    os_info VARCHAR(100),
    browser_info VARCHAR(100),
    is_trusted BOOLEAN DEFAULT FALSE NOT NULL,
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_seen_ip INET,
    usage_count INTEGER DEFAULT 1 NOT NULL,

    -- Constraints
    CONSTRAINT chk_usage_count_positive CHECK (usage_count > 0),
    CONSTRAINT chk_device_dates CHECK (last_seen_at >= first_seen_at)
);

-- Transaction monitoring rules and alerts
CREATE TABLE monitoring_rules (
    rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_name VARCHAR(100) UNIQUE NOT NULL,
    rule_type VARCHAR(50) NOT NULL, -- 'velocity', 'amount', 'pattern', 'geo', etc.
    conditions JSONB NOT NULL, -- Rule configuration in JSON
    risk_score INTEGER NOT NULL, -- Score assigned when rule triggers
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,

    -- Constraints
    CONSTRAINT chk_monitoring_risk_score CHECK (risk_score >= 1 AND risk_score <= 100)
);

-- Rule violations and alerts
CREATE TABLE rule_violations (
    violation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id UUID NOT NULL REFERENCES monitoring_rules(rule_id) ON DELETE RESTRICT,
    customer_id UUID REFERENCES customers(customer_id) ON DELETE CASCADE,
    account_id UUID REFERENCES accounts(account_id) ON DELETE CASCADE,
    transaction_id UUID REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    violation_data JSONB NOT NULL, -- Details of what triggered the rule
    risk_score INTEGER NOT NULL,
    status VARCHAR(50) DEFAULT 'open' NOT NULL,
    reviewed_by VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    reviewed_at TIMESTAMP WITH TIME ZONE,

    -- Constraints
    CONSTRAINT chk_violation_risk_score CHECK (risk_score >= 1 AND risk_score <= 100),
    CONSTRAINT chk_violation_status CHECK (status IN ('open', 'investigating', 'resolved', 'false_positive'))
);

-- Indexes for audit_logs table
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at);
CREATE INDEX idx_audit_logs_action ON audit_logs(action);
CREATE INDEX idx_audit_logs_ip ON audit_logs(ip_address);

-- Indexes for user_sessions table
CREATE INDEX idx_user_sessions_customer_id ON user_sessions(customer_id);
CREATE INDEX idx_user_sessions_token ON user_sessions(session_token);
CREATE INDEX idx_user_sessions_active ON user_sessions(is_active, expires_at);
CREATE INDEX idx_user_sessions_ip ON user_sessions(ip_address);

-- Indexes for failed_login_attempts table
CREATE INDEX idx_failed_logins_email ON failed_login_attempts(email);
CREATE INDEX idx_failed_logins_ip ON failed_login_attempts(ip_address);
CREATE INDEX idx_failed_logins_created_at ON failed_login_attempts(created_at);

-- Indexes for suspicious_activities table
CREATE INDEX idx_suspicious_activities_customer ON suspicious_activities(customer_id);
CREATE INDEX idx_suspicious_activities_account ON suspicious_activities(account_id);
CREATE INDEX idx_suspicious_activities_type ON suspicious_activities(activity_type);
CREATE INDEX idx_suspicious_activities_risk ON suspicious_activities(risk_score);
CREATE INDEX idx_suspicious_activities_status ON suspicious_activities(status);

-- Indexes for known_devices table
CREATE INDEX idx_known_devices_customer_id ON known_devices(customer_id);
CREATE INDEX idx_known_devices_fingerprint ON known_devices(device_fingerprint);
CREATE INDEX idx_known_devices_trusted ON known_devices(is_trusted);

-- Indexes for monitoring_rules table
CREATE INDEX idx_monitoring_rules_type ON monitoring_rules(rule_type);
CREATE INDEX idx_monitoring_rules_active ON monitoring_rules(is_active);

-- Indexes for rule_violations table
CREATE INDEX idx_rule_violations_rule_id ON rule_violations(rule_id);
CREATE INDEX idx_rule_violations_customer ON rule_violations(customer_id);
CREATE INDEX idx_rule_violations_transaction ON rule_violations(transaction_id);
CREATE INDEX idx_rule_violations_risk ON rule_violations(risk_score);
CREATE INDEX idx_rule_violations_status ON rule_violations(status);