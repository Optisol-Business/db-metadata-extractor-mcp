# Docker Publishing Troubleshooting Guide

Common issues and solutions when publishing the MCP server to Docker Hub.

---

## Build Issues

### ❌ Error: "docker: command not found"

**Cause:** Docker is not installed or not in PATH

**Solution:**
```bash
# Install Docker
# Windows/macOS: https://www.docker.com/products/docker-desktop
# Linux: sudo apt-get install docker.io

# Verify installation
docker --version
# Expected: Docker version 20.10 or higher
```

---

### ❌ Error: "Cannot connect to Docker daemon"

**Cause:** Docker daemon is not running

**Solution:**
```bash
# macOS/Windows
# Open Docker Desktop application and wait for it to fully start
# Check system tray for Docker icon

# Linux
sudo systemctl start docker
sudo usermod -aG docker $USER  # Add current user to docker group
newgrp docker  # Activate the new group membership
```

---

### ❌ Error: "Permission denied while trying to connect to Docker daemon"

**Cause:** Current user doesn't have Docker permissions

**Solution:**
```bash
# Linux only
sudo usermod -aG docker $USER
newgrp docker
docker ps  # Verify it works
```

---

### ❌ Error: "failed to solve: E: Unable to locate package psycopg2-binary"

**Cause:** System dependencies missing during build

**Solution:**
```bash
# Rebuild with fresh layers
docker build --no-cache -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .

# Or check Dockerfile for required system packages
# Ensure lines like these exist:
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     build-essential \
#     libpq-dev \
```

---

### ❌ Error: "Dockerfile not found"

**Cause:** Dockerfile is not in the current directory

**Solution:**
```bash
# Navigate to project root
cd db-metadata-extractor-mcp

# Verify Dockerfile exists
ls -la Dockerfile  # macOS/Linux
Test-Path ./Dockerfile  # Windows PowerShell

# Build from correct directory
docker build -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .
```

---

### ❌ Error: "no space left on device"

**Cause:** Docker images are too large or disk is full

**Solution:**
```bash
# Check disk space
df -h  # macOS/Linux
wmic logicaldisk get name,size,freespace  # Windows

# Clean up old Docker images and containers
docker system prune -a --volumes  # Removes all unused images

# Free up space and rebuild
docker build --no-cache -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .
```

---

### ❌ Error: "Multiple tasks with name 'python:3.11-slim'"

**Cause:** Dockerfile syntax error or corrupted build

**Solution:**
```bash
# Clean up and verify Dockerfile
docker system prune -a

# Check Dockerfile syntax
cat Dockerfile | head -20
# Should start with: FROM python:3.11-slim as builder

# Rebuild
docker build --progress=plain -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .
```

---

## Push Issues

### ❌ Error: "denied: requested access is denied"

**Cause:** Not logged in to Docker Hub or incorrect credentials

**Solution:**
```bash
# Logout and login again
docker logout
docker login
# Username: optisolbusiness
# Password: [your password]
# Expected: Login Succeeded

# Verify username
docker info
# Should show: Username: optisolbusiness
```

---

### ❌ Error: "Error saving credentials: error storing credentials in native keychain"

**Cause:** Keychain/credential storage issue

**Solution:**
```bash
# Option 1: Use PAT (Personal Access Token)
# 1. Create PAT at https://hub.docker.com/settings/security
# 2. Use it as password:
echo "YOUR_PAT_TOKEN" | docker login -u optisolbusiness --password-stdin

# Option 2: Use config file directly (less secure)
# Create ~/.docker/config.json with base64-encoded credentials
# (Not recommended - use PAT instead)
```

---

### ❌ Error: "no basic auth credentials"

**Cause:** Not authenticated for push operation

**Solution:**
```bash
# Re-login to Docker Hub
docker logout
docker login optisolbusiness
# Enter password or PAT

# Verify connection
docker push optisolbusiness/db-metadata-extractor-mcp:test
# Should work if logged in
```

---

### ❌ Error: "image not found" when pushing

**Cause:** Image doesn't exist locally

**Solution:**
```bash
# List images
docker images | grep db-metadata-extractor-mcp
# Should show the image you want to push

# If not found, rebuild it first
docker build -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .

# Then push
docker push optisolbusiness/db-metadata-extractor-mcp:0.1.2
```

---

### ❌ Error: "retries exceeded while pushing" or timeout

**Cause:** Network issue or Docker Hub is slow

**Solution:**
```bash
# Wait a few minutes and retry
sleep 300  # Linux/macOS
Start-Sleep -Seconds 300  # Windows PowerShell

# Retry push
docker push optisolbusiness/db-metadata-extractor-mcp:0.1.2

# Or check Docker Hub status
# https://status.docker.com/
```

---

### ❌ Error: "reference not found"

**Cause:** Tag doesn't match what you're trying to push

**Solution:**
```bash
# List all tags
docker images | grep db-metadata-extractor-mcp

# Create correct tag if missing
docker tag optisolbusiness/db-metadata-extractor-mcp:0.1.2 \
           optisolbusiness/db-metadata-extractor-mcp:latest

# Then push
docker push optisolbusiness/db-metadata-extractor-mcp:0.1.2
```

---

## Runtime Issues

### ❌ Error: "Container exits immediately" or "exec user process caused: exec format error"

**Cause:** ENTRYPOINT is incorrect or Python module not found

**Solution:**
```bash
# Test with bash shell to debug
docker run --rm -it optisolbusiness/db-metadata-extractor-mcp:0.1.2 /bin/bash

# Inside container, try running the module
python -m db_metadata_extractor_mcp.server --help

# If it fails, check:
# 1. Dockerfile has correct COPY command
# 2. Python package is installed in virtual environment
# 3. ENTRYPOINT syntax is correct

# Example fix
# Check these in Dockerfile:
# COPY --chown=mcpuser:mcpuser src/ /app/src/
# ENTRYPOINT ["python", "-m", "db_metadata_extractor_mcp.server"]
```

---

### ❌ Error: "Module not found: db_metadata_extractor"

**Cause:** Python package not installed in image

**Solution:**
```bash
# Check if package is in requirements.txt
grep -i "mcp" requirements.txt  # Should see dependencies

# Verify Dockerfile installs from requirements.txt
cat Dockerfile | grep -A 2 "requirements.txt"
# Should have: COPY requirements.txt .
#              RUN pip install -r requirements.txt

# Rebuild
docker build --no-cache -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .
```

---

### ❌ Error: "Command './db-metadata-extractor-mcp' not found"

**Cause:** Entry point is trying to run a script that doesn't exist

**Solution:**
```bash
# Check what entry point is defined
docker inspect optisolbusiness/db-metadata-extractor-mcp:0.1.2 | grep -A 2 Entrypoint

# It should be:
# "Entrypoint": ["python", "-m", "db_metadata_extractor_mcp.server"]

# If it's trying to run a script, change the Dockerfile to use Python module instead
# Fix Dockerfile:
# ENTRYPOINT ["python", "-m", "db_metadata_extractor_mcp.server"]
# NOT:
# ENTRYPOINT ["./db-metadata-extractor-mcp"]
```

---

### ❌ Error: "PYTHONUNBUFFERED not set" - No output from container

**Cause:** Python buffering prevents stdio output

**Solution:**
```bash
# Verify Dockerfile has:
# ENV PYTHONUNBUFFERED=1

# Rebuild if missing
docker build --no-cache -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .

# Test output
docker run --rm optisolbusiness/db-metadata-extractor-mcp:0.1.2 --help
```

---

### ❌ Error: "Permission denied" when running as non-root

**Cause:** Files have wrong ownership or permissions

**Solution:**
```bash
# Verify Dockerfile has correct chown
cat Dockerfile | grep "chown"
# Should include: COPY --chown=mcpuser:mcpuser src/ /app/src/

# Or after COPY, fix permissions
# RUN chown -R mcpuser:mcpuser /app

# Rebuild
docker build --no-cache -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .
```

---

### ❌ Error: "Cannot find database", "Connection refused", "Authentication failed"

**Cause:** Database credentials or connection string is wrong

**Solution:**
```bash
# When running, pass environment variables
docker run --rm \
  -e DATABASE_URL="postgresql://user:pass@host:5432/db" \
  -e POSTGRES_USER="user" \
  -e POSTGRES_PASSWORD="pass" \
  optisolbusiness/db-metadata-extractor-mcp:0.1.2

# Or use .env file
docker run --rm --env-file .env \
  optisolbusiness/db-metadata-extractor-mcp:0.1.2

# Or use docker-compose
docker-compose up
```

---

## Registry Issues

### ❌ Error: "Image not showing on Docker Hub" after 10+ minutes

**Cause:** Registry is slow or image didn't finish pushing

**Solution:**
```bash
# Check push completed successfully
docker push optisolbusiness/db-metadata-extractor-mcp:0.1.2
# Should see "digest: sha256:..." at end

# Wait 5-10 minutes for Docker Hub to process
# Then refresh: https://hub.docker.com/r/optisolbusiness/db-metadata-extractor-mcp

# Or pull fresh to verify it's on hub
docker pull optisolbusiness/db-metadata-extractor-mcp:0.1.2
# Should download from Docker Hub, not use local cache
```

---

### ❌ Error: "Image shows on Docker Hub but not in MCP Registry"

**Cause:** MCP Registry hasn't synced yet or server.json is invalid

**Solution:**
```bash
# 1. Wait 1-2 hours (registry syncs hourly)

# 2. Verify server.json is valid JSON
cat server.json | jq .
# Should parse without errors

# 3. Verify server.json has both pypi and oci entries
grep -A 3 "registryType" server.json
# Should show both "pypi" and "oci"

# 4. Check versions match
grep "version" server.json
# All should say "0.1.2" (or whatever version)

# 5. Commit to GitHub if you made changes
git add server.json
git commit -m "Update server.json versions"
git push origin main

# 6. Manually update if auto-sync fails
# Create issue: https://github.com/modelcontextprotocol/servers
```

---

### ❌ Error: "Cannot decode JWT" in MCP Registry

**Cause:** server.json has invalid schema

**Solution:**
```bash
# Validate against schema
curl https://static.modelcontextprotocol.io/schemas/2025-07-09/server.schema.json > schema.json

# Validate your server.json
npm install -g ajv-cli
ajv validate -s schema.json -d server.json
# Should show "valid" or detailed errors

# Or use online JSON Schema Validator
# https://www.jsonschemavalidator.net/
```

---

## Verification Checklist

After troubleshooting, verify these:

```bash
# 1. Docker is running
docker ps
# Expected: No errors

# 2. Image exists locally
docker images | grep db-metadata-extractor-mcp
# Expected: Shows tags 0.1.2 and latest

# 3. Image runs
docker run --rm optisolbusiness/db-metadata-extractor-mcp:0.1.2 --help
# Expected: Help message

# 4. Logged in to Docker Hub
docker info | grep Username
# Expected: optisolbusiness

# 5. Image is on Docker Hub
curl -s https://registry.hub.docker.com/v2/repositories/optisolbusiness/db-metadata-extractor-mcp/ | grep name
# Expected: JSON with repository info

# 6. server.json is valid
cat server.json | jq .
# Expected: No JSON errors

# 7. server.json on GitHub (if not already)
git log --oneline | head -5
# Should show recent commits with server.json
```

---

## Still Stuck?

1. **Check Docker logs:**
   ```bash
   docker logs <container_id>
   ```

2. **Run with verbose output:**
   ```bash
   docker build --progress=plain -t optisolbusiness/db-metadata-extractor-mcp:0.1.2 .
   docker push -v optisolbusiness/db-metadata-extractor-mcp:0.1.2
   ```

3. **Inspect image:**
   ```bash
   docker inspect optisolbusiness/db-metadata-extractor-mcp:0.1.2
   ```

4. **Test with shell:**
   ```bash
   docker run --rm -it optisolbusiness/db-metadata-extractor-mcp:0.1.2 /bin/bash
   ```

5. **Check system resources:**
   ```bash
   docker system df  # Disk usage
   docker stats  # Running container stats
   ```

6. **Ask for help:**
   - GitHub Issues: https://github.com/Optisol-Business/db-metadata-extractor-mcp/issues
   - Docker Support: https://docs.docker.com/support/
   - MCP Support: https://github.com/modelcontextprotocol/servers/discussions

---

**Most issues are fixed by:** rebuilding without cache → re-logging in → retrying push
