# Docker Publishing - 8 Simple Steps

## Prerequisites Check

```powershell
docker --version          # Must work
docker ps                 # Must work
# Docker Hub account: https://hub.docker.com/signup
```

---

## Step 1: Build Image 

```powershell
cd "c:\path\to\db-metadata-extractor-mcp"
.\docker-publish.ps1 -SkipPush
```

Wait for: ✓ Image built successfully

---

## Step 2: Test Image 

```powershell
docker run --rm optisolbusiness/db-metadata-extractor-mcp:0.1.2 --help
```

Should run without errors.

---

## Step 3: Login to Docker Hub 

```powershell
docker login
```
- Username: `optisolbusiness`
- Password: Your Docker Hub password

---

## Step 4: Push Image 

```powershell
.\docker-publish.ps1
```

Or manually:
```powershell
docker push optisolbusiness/db-metadata-extractor-mcp:0.1.2
docker push optisolbusiness/db-metadata-extractor-mcp:latest
```

---

## Step 5: Verify on Docker Hub 

Visit: https://hub.docker.com/r/optisolbusiness/db-metadata-extractor-mcp

Check: Both tags visible (0.1.2 and latest)

---

## Step 6: Commit to GitHub 

```bash
git add .
git commit -m "Release v0.1.2 with Docker support"
git push origin main
```

---

## Step 7: Wait for Registry Sync 

Registry auto-syncs from GitHub. Nothing to do. Just wait.

---

## Step 8: Verify in Marketplace

Visit: https://registry.modelcontextprotocol.io/

Search for: "db-metadata-extractor-mcp"

Both options should appear:
- ✓ PyPI: pip install db-metadata-extractor-mcp
- ✓ Docker: docker pull optisolbusiness/db-metadata-extractor-mcp:0.1.2

---

## Issues?

See: **DOCKER_TROUBLESHOOTING.md**

---

**Total time: ~50 minutes (+ 1-2 hour auto-sync)**
