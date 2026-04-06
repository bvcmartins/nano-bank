# Nano Bank Kubernetes Setup

This directory contains Kubernetes manifests to run PostgreSQL locally for the Nano Bank core system.

## Prerequisites

1. **Docker** - Container runtime
2. **kubectl** - Kubernetes CLI tool
3. **Kind** - Kubernetes in Docker (or Minikube/Docker Desktop)

## Quick Start

### 1. Start Docker (if needed)
```bash
sudo systemctl start docker
sudo usermod -aG docker $USER
# Log out and back in for group changes
```

### 2. Create Kind Cluster
```bash
kind create cluster --config kind-cluster-config.yaml
```

### 3. Deploy PostgreSQL
```bash
./deploy.sh
```

## What Gets Deployed

### Infrastructure
- **Namespace**: `nano-bank` - Isolated environment
- **Secret**: Database credentials (encrypted)
- **ConfigMap**: PostgreSQL configuration + SQL scripts
- **PVC**: 10GB persistent storage for database files

### Database
- **PostgreSQL 16** with banking-optimized settings
- **Health checks** for reliability
- **Resource limits** for production-like behavior
- **Security context** with non-root user

### Networking
- **ClusterIP Service**: Internal cluster access
- **NodePort Service**: External access on port 30432

### Initialization
- **Job**: Automatically runs all DDL scripts to create banking schema
- **Executes in order**: enums → customers → accounts → transactions → security → triggers

## Connection Details

**External Access (from your machine):**
```
Host: localhost
Port: 30432
Database: nano_bank_db
Username: nanobank_user
Password: secure_nano_password_2024!
```

**Connect with psql:**
```bash
psql -h localhost -p 30432 -U nanobank_user -d nano_bank_db
```

**Internal Access (from other pods):**
```
Host: postgres-service.nano-bank.svc.cluster.local
Port: 5432
```

## Useful Commands

### Cluster Management
```bash
# Check cluster status
kubectl cluster-info

# List all resources
kubectl get all -n nano-bank

# View PostgreSQL logs
kubectl logs -n nano-bank deployment/postgres

# Access PostgreSQL shell
kubectl exec -it -n nano-bank deployment/postgres -- psql -U nanobank_user -d nano_bank_db
```

### Database Operations
```bash
# Check database initialization job
kubectl get job -n nano-bank
kubectl logs -n nano-bank job/init-db

# Monitor PostgreSQL pod
kubectl get pods -n nano-bank -w

# Port forward for direct access
kubectl port-forward -n nano-bank service/postgres-service 5432:5432
```

### Cleanup
```bash
# Delete everything
kubectl delete namespace nano-bank

# Delete cluster
kind delete cluster --name nano-bank
```

## Banking Schema Features

The deployed database includes:

✅ **Complete banking schema** with Canadian compliance
✅ **Double-entry bookkeeping** with automatic validation
✅ **Audit trails** for all changes
✅ **Security monitoring** tables
✅ **KYC/AML** compliance features
✅ **Transaction limits** and monitoring
✅ **Automatic triggers** for business logic

## Performance Configuration

The PostgreSQL instance is optimized for banking workloads:

- **256MB shared_buffers** for caching
- **WAL replication level** for durability
- **Audit logging** enabled
- **Toronto timezone** configured
- **SCRAM-SHA-256** password encryption

## Security Features

- **Non-root containers** for security
- **Encrypted secrets** for credentials
- **Resource limits** to prevent resource exhaustion
- **Health checks** for reliability
- **Persistent storage** for data durability

This setup provides a production-like PostgreSQL environment for developing your challenger bank backend!