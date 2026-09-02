import "server-only";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8081";

// The Agentic Branch API (agent/api.py) — service-token authenticated, never
// exposed to the browser. AGENT_SERVICE_TOKEN mirrors agent/.env's
// BRANCH_SERVICE_TOKEN and is only populated in-cluster (see k8s/ui-deployment.yaml).
export const AGENT_API_URL = process.env.NEXT_PUBLIC_AGENT_API_URL || "http://localhost:8086";
export const AGENT_SERVICE_TOKEN = process.env.AGENT_SERVICE_TOKEN || "";

// 7 days — the refresh-token cookie's lifetime; mirrors api/config/default.toml's
// jwt.refresh_expires_in. This is NOT used for the access-token cookie: that one
// is set from the login/refresh response's own `expires_in` (~15 min) so the
// cookie expires with the JWT it holds rather than outliving it by days.
export const REFRESH_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 7;
