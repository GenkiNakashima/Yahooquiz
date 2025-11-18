-- Migration: Create core tables for Yahooquiz authentication and game features
-- Description: Initial schema with users, matches, points, icons

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    points BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create index on email for faster lookups
CREATE INDEX idx_users_email ON users(email);

-- Auth refresh tokens table
CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked BOOLEAN NOT NULL DEFAULT false
);

-- Create index on user_id and token_hash
CREATE INDEX idx_refresh_tokens_user_id ON auth_refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_token_hash ON auth_refresh_tokens(token_hash);

-- Matches table
CREATE TABLE IF NOT EXISTS matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    match_type TEXT NOT NULL DEFAULT '1v1',
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

-- Create index on created_at for sorting
CREATE INDEX idx_matches_created_at ON matches(created_at DESC);

-- Match players table
CREATE TABLE IF NOT EXISTS match_players (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score INTEGER NOT NULL DEFAULT 0,
    is_winner BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create indexes for faster lookups
CREATE INDEX idx_match_players_match_id ON match_players(match_id);
CREATE INDEX idx_match_players_user_id ON match_players(user_id);

-- Points transactions table
CREATE TABLE IF NOT EXISTS points_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount BIGINT NOT NULL,
    type TEXT NOT NULL,
    reference_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create indexes
CREATE INDEX idx_points_transactions_user_id ON points_transactions(user_id);
CREATE INDEX idx_points_transactions_created_at ON points_transactions(created_at DESC);

-- Icons table
CREATE TABLE IF NOT EXISTS icons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    price BIGINT NOT NULL DEFAULT 0,
    is_limited BOOLEAN NOT NULL DEFAULT false,
    total_supply INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- User icons table (purchased icons)
CREATE TABLE IF NOT EXISTS user_icons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    icon_id UUID NOT NULL REFERENCES icons(id) ON DELETE CASCADE,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, icon_id)
);

-- Create indexes
CREATE INDEX idx_user_icons_user_id ON user_icons(user_id);
CREATE INDEX idx_user_icons_icon_id ON user_icons(icon_id);

-- Audit logs table (optional, for security tracking)
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id UUID,
    metadata JSONB,
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create index on created_at for log queries
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);
CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);

-- Insert default icons
INSERT INTO icons (name, description, price, is_limited, total_supply) VALUES
    ('bronze_badge', 'ブロンズバッジ', 100, false, NULL),
    ('silver_badge', 'シルバーバッジ', 500, false, NULL),
    ('gold_badge', 'ゴールドバッジ', 1000, false, NULL),
    ('platinum_badge', 'プラチナバッジ', 2500, true, 100),
    ('diamond_badge', 'ダイヤモンドバッジ', 5000, true, 50),
    ('quiz_master', 'クイズマスター', 10000, true, 10)
ON CONFLICT (name) DO NOTHING;

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger to auto-update updated_at on users table
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Comments for documentation
COMMENT ON TABLE users IS 'User accounts with authentication credentials';
COMMENT ON TABLE auth_refresh_tokens IS 'JWT refresh tokens for authentication';
COMMENT ON TABLE matches IS 'Quiz match/game sessions';
COMMENT ON TABLE match_players IS 'Players participating in matches';
COMMENT ON TABLE points_transactions IS 'History of all point changes';
COMMENT ON TABLE icons IS 'Available icons/items for purchase';
COMMENT ON TABLE user_icons IS 'Icons owned by users';
COMMENT ON TABLE audit_logs IS 'Security and action audit trail';
