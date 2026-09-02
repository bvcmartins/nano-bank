#!/bin/bash
set -e

echo "🏦 Deploying Nano Bank PostgreSQL to Kubernetes..."

# Ensure we're in the right directory
cd "$(dirname "$0")"

# Ensure cluster A exists AND publishes host port 3000 (needed for the UI).
# kind fixes port mappings at creation time, so a cluster made before the UI
# mapping was added must be recreated (Postgres data is wiped; the init-db job
# below re-creates the schema).
if kind get clusters 2>/dev/null | grep -qx "nano-bank"; then
  if ! docker port nano-bank-control-plane 2>/dev/null | grep -q ':3000'; then
    echo "⚠️  Existing 'nano-bank' cluster lacks the UI port 3000 mapping."
    echo "    Recreating it — Postgres data will be wiped and the schema re-initialised."
    kind delete cluster --name nano-bank
    kind create cluster --config kind-cluster-config.yaml
  fi
else
  echo "📦 Creating 'nano-bank' cluster..."
  kind create cluster --config kind-cluster-config.yaml
fi
kubectl config use-context kind-nano-bank >/dev/null

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl not found in PATH"
    exit 1
fi

# Check if Kind cluster is running
if ! kubectl cluster-info &> /dev/null; then
    echo "❌ Kubernetes cluster not accessible"
    echo "💡 Run: kind create cluster --config kind-cluster-config.yaml"
    exit 1
fi

echo "✅ Kubernetes cluster is accessible"

# Create namespace
echo "📁 Creating namespace..."
kubectl apply -f postgres-namespace.yaml

# Create secrets and config
echo "🔐 Creating secrets and configuration..."
kubectl apply -f postgres-secret.yaml
kubectl apply -f postgres-configmap.yaml

# Create SQL scripts configmap
echo "📜 Creating SQL scripts configmap..."
kubectl create configmap sql-scripts \
    --namespace=nano-bank \
    --from-file=../src/core/tables/ \
    --dry-run=client -o yaml | kubectl apply -f -

# Create persistent volume
echo "💾 Creating persistent storage..."
kubectl apply -f postgres-pvc.yaml

# Deploy PostgreSQL
echo "🐘 Deploying PostgreSQL..."
kubectl apply -f postgres-deployment.yaml
kubectl apply -f postgres-service.yaml

# Wait for PostgreSQL to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
kubectl wait --namespace=nano-bank \
    --for=condition=ready pod \
    --selector=app=postgres \
    --timeout=300s

# Initialize database schema
echo "🏗️  Initializing database schema..."
kubectl apply -f init-db-job.yaml

# Wait for job completion
echo "⏳ Waiting for database initialization..."
kubectl wait --namespace=nano-bank \
    --for=condition=complete job/init-db \
    --timeout=120s

# --- bank-api (in-cluster), wired cross-cluster to the modern core ---
echo "🐳 Building + loading bank-api image..."
docker build -t nano-bank-api:dev ../api
kind load docker-image nano-bank-api:dev --name nano-bank

echo "🌉 Wiring cross-cluster route to modern-core (host gateway hop)..."
# The "kind" docker network has both an IPv4 and IPv6 gateway; grab the IPv4
# one specifically. The modern-core cluster's hostPort publish (8191->30091)
# is IPv4-only, so wiring the IPv6 gateway leaves bank-api unable to reach it.
GATEWAY_IP=$(docker network inspect kind -f '{{range .IPAM.Config}}{{if .Gateway}}{{.Gateway}} {{end}}{{end}}' | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
echo "   host gateway = ${GATEWAY_IP} (core published on host :8191 by cluster modern-core)"
sed "s/__GATEWAY_IP__/${GATEWAY_IP}/" modern-core-endpoints.yaml.tmpl | kubectl apply -f -

echo "🏦 Deploying bank-api..."
kubectl apply -f bank-api-deployment.yaml
kubectl -n nano-bank rollout status deploy/bank-api --timeout=180s

# The web UI is optional for backend/agent workflows (e.g. the COO demo, which
# never touches :3000). SKIP_UI=1 skips building/deploying it so an unrelated UI
# build break can't abort a bank+agent bring-up.
if [ "${SKIP_UI:-0}" = "1" ]; then
  echo "⏭️  SKIP_UI=1 — skipping nano-bank-ui build/deploy."
else
  echo "🐳 Building + loading nano-bank-ui image..."
  docker build -t nano-bank-ui:dev ../ui
  kind load docker-image nano-bank-ui:dev --name nano-bank

  echo "🖥️  Deploying nano-bank-ui..."
  kubectl apply -f ui-deployment.yaml
  kubectl -n nano-bank rollout status deploy/nano-bank-ui --timeout=180s
fi

echo "🎉 Nano Bank PostgreSQL deployment complete!"
echo ""
echo "📊 Connection Details:"
echo "  Host: localhost"
echo "  Port: 30432 (NodePort)"
echo "  Database: nano_bank_db"
echo "  Username: nanobank_user"
echo "  Password: secure_nano_password_2024!"
echo ""
echo "🔗 Connect with:"
echo "  psql -h localhost -p 30432 -U nanobank_user -d nano_bank_db"
echo ""
echo "  UI:                http://localhost:3000"
echo ""
echo "📈 Useful Commands:"
echo "  kubectl get pods -n nano-bank"
echo "  kubectl logs -n nano-bank deployment/postgres"
echo "  kubectl exec -it -n nano-bank deployment/postgres -- psql -U nanobank_user -d nano_bank_db"