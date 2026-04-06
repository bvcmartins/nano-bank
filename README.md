# nano-bank
Why not vibe coding a bank?

## Running

### Prerequisites

On first run, ensure `kubectl` and `kind` are in your PATH and Docker permissions are set up:

```bash
export PATH="$HOME/bin:$PATH"
./setup-k8s.sh
```

### Start

```bash
./start-nano-bank.sh
```

This will:
1. Create a `kind` Kubernetes cluster (if not already running) and deploy PostgreSQL
2. Set up port-forwarding from the cluster's Postgres to `localhost:5432`
3. Start the Rust API server on `http://localhost:8081`

### Stop

```bash
./stop-nano-bank.sh
```

## Services

| Service      | URL                              |
|--------------|----------------------------------|
| API Server   | http://localhost:8081            |
| Health Check | http://localhost:8081/health     |
| API Docs     | http://localhost:8081/docs       |
| PostgreSQL   | localhost:5432                   |

## Logs

```bash
tail -f /tmp/nano-bank-api.log
tail -f /tmp/nano-bank-port-forward.log
```
