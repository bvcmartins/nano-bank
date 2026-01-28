Now, to fix the kubectl issue and set up Kubernetes, please run these commands
   in your terminal:

  🔧 Fix kubectl PATH and Docker permissions:

  # 1. Fix the PATH issue for kubectl
  export PATH="$HOME/bin:$PATH"

  # 2. Run the setup script to handle Docker permissions
  cd ~/dev/nano-bank
  ./setup-k8s.sh

  🐳 If Docker needs manual setup:

  If the script indicates Docker permission issues, run these commands:

  # Start Docker daemon
  sudo systemctl start docker

  # Add your user to docker group
  sudo usermod -aG docker $USER

  # Refresh group membership (alternative to logging out/in)
  newgrp docker

  🚀 Once everything is set up:

  # Deploy the PostgreSQL cluster
  cd ~/dev/nano-bank
  export PATH="$HOME/bin:$PATH"
  ./k8s/deploy.sh

  📝 Alternative: Manual Commands

  If you prefer to run commands manually instead of the script:

  # Export PATH
  export PATH="$HOME/bin:$PATH"

  # Verify tools
  kubectl version --client
  kind version

  # Create the cluster
  kind create cluster --config k8s/kind-cluster-config.yaml

  # Deploy PostgreSQL
  ./k8s/deploy.sh

  The main issues were:
  1. PATH: kubectl and kind were installed to ~/bin but not in your PATH
  2. Docker permissions: Your user needs to be in the docker group to use Docker
   without sudo

  Once you fix these, the deployment should work perfectly! The setup script I
  created will handle both issues automatically.

  cd ~/dev/nano-bank/api
cargo run
2. Once running, test the endpoints: Based on main.rs, you'll have these endpoints:
# Health check
curl http://localhost:8080/health

# API documentation
curl http://localhost:8080/docs

# Authentication
curl -X POST http://localhost:8080/api/v1/auth/...

# Customers
curl http://localhost:8080/api/v1/customers

# Accounts
curl http://localhost:8080/api/v1/accounts

# Transactions
curl http://localhost:8080/api/v1/transactions

# Security
curl http://localhost:8080/api/v1/security
3. Check logs from the Rust server - it will show you the exact routes and any errors. Note: The server config shows it expects PostgreSQL to be running (from your k8s deployment), so make sure that's still up with kubectl get pods. Want me to help you start the server and create some test requests?
