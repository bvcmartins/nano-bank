#!/bin/bash
set -e

project_path=$(cd "$(dirname "$0")/.." && pwd)
cd "$project_path"

echo "♻️  Recreating Nano-Bank Kubernetes Cluster from Scratch..."
echo ""

# Step 1: Delete stale Kind cluster if it exists
echo "🧹 Deleting existing cluster..."
kind delete cluster --name nano-bank || true
sleep 2

# Step 2: Create a fresh Kind cluster with custom port mappings
echo "📦 Creating fresh Kind Kubernetes cluster..."
kind create cluster --config k8s/kind-cluster-config.yaml

# Step 3: Sideload PostgreSQL image natively into containerd on nodes
# (This completely bypasses corporate TLS proxy/firewall image pull certificate errors!)
echo "🚚 Sideloading PostgreSQL image natively into cluster nodes..."
if docker image inspect postgres:16-alpine >/dev/null 2>&1; then
    echo "✅ postgres:16-alpine is already cached on host."
else
    echo "📥 Pulling postgres:16-alpine on host..."
    docker pull postgres:16-alpine
fi

echo "🔄 Importing postgres image into Kind nodes..."
docker save postgres:16-alpine | docker exec -i nano-bank-control-plane ctr -n k8s.io images import - || true
docker save postgres:16-alpine | docker exec -i nano-bank-worker ctr -n k8s.io images import - || true
docker save postgres:16-alpine | docker exec -i nano-bank-worker2 ctr -n k8s.io images import - || true

# Step 4: Deploy PostgreSQL
echo "🚀 Deploying PostgreSQL service and schemas..."
./k8s/deploy.sh

# Step 5: Wait for pod to be ready and initialize tables
echo "⏳ Waiting for PostgreSQL pod to be ready..."
kubectl wait --namespace=nano-bank --for=condition=ready pod --selector=app=postgres --timeout=45s

echo "⏳ Waiting for Database initialization job to complete..."
if kubectl wait --namespace=nano-bank --for=condition=complete job/init-db --timeout=45s; then
    echo ""
    echo "🎉 SUCCESS: Cluster rebuilt, PostgreSQL is running, and database is seeded!"
    echo "🚀 Now you can start the API by running:"
    echo "   ./scripts/start-dev.sh"
else
    echo ""
    echo "⚠️ Database initialization timed out. You may need to trigger it manually."
    exit 1
fi
