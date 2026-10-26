-- D12, the base of stage 1: spending limits for all paid models together or for one provider, per period.
-- Spending itself is the usage table; a budget only says how much of it is allowed. Projects, agent roles and
-- tasks get their own scopes when they exist (stages 2-5).

CREATE TABLE budgets (
    id          INTEGER PRIMARY KEY,
    scope       TEXT NOT NULL CHECK (scope IN ('total', 'provider')),
    scope_ref   TEXT NOT NULL DEFAULT '',  -- the provider for scope 'provider' ('anthropic'); '' for 'total'
    period      TEXT NOT NULL CHECK (period IN ('day', 'week', 'month')),  -- local calendar day / week / month
    soft_usd    REAL CHECK (soft_usd IS NULL OR soft_usd >= 0),  -- reaching it: a warning
    hard_usd    REAL CHECK (hard_usd IS NULL OR hard_usd >= 0),  -- reaching it: no new paid requests...
    hard_action TEXT NOT NULL DEFAULT 'ask' CHECK (hard_action IN ('ask', 'block')),  -- ...unless confirmed
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (soft_usd IS NOT NULL OR hard_usd IS NOT NULL),
    CHECK ((scope = 'total') = (scope_ref = '')),
    UNIQUE (scope, scope_ref, period)
);

-- spending since a moment, for one provider's models ('anthropic/%') or all
CREATE INDEX usage_model_ts ON usage (model_id, ts);
