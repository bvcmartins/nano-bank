#!/bin/bash
set -e

echo "🏦 Deploying Nano Bank PostgreSQL to Kubernetes..."

# Ensure we're in the right directory
cd "$(dirname "$0")"

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
echo "📈 Useful Commands:"
echo "  kubectl get pods -n nano-bank"
echo "  kubectl logs -n nano-bank deployment/postgres"
echo "  kubectl exec -it -n nano-bank deployment/postgres -- psql -U nanobank_user -d nano_bank_db"