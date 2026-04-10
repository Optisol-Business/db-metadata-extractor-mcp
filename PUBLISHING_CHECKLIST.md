# MCP Registry Publishing Checklist

## ✅ Pre-Publication Checklist

### 1. Code & Package
- ✅ `pyproject.toml` - Modern PEP 517/518 config
- ✅ `server.py` - MCP server implementation with tools
- ✅ `requirements.txt` - Dependencies listed
- ✅ Published to PyPI: `db-metadata-extractor-mcp`
- ✅ Package installable: `pip install db-metadata-extractor-mcp`

### 2. MCP Registry Files
- ✅ `server.json` - Valid JSON with tool definitions
- ✅ `SERVER_README.md` - User documentation
- ✅ `MCP_REGISTRY_GUIDE.md` - Publishing guide

### 3. GitHub Setup
- ⬜ GitHub account created
- ⬜ Repository created: `yourusername/db-metadata-extractor-mcp`
- ⬜ Repository is PUBLIC
- ⬜ LICENSE file added (MIT)
- ⬜ Code pushed to GitHub

### 4. GitHub PAT (Personal Access Token)
- ⬜ GitHub PAT created at https://github.com/settings/tokens
- ⬜ Scopes: `public_repo`, `user:email`
- ⬜ Token saved: `ghp_XXXXXXXXXXXXXXX`
- ⬜ Environment variable set: `$env:GITHUB_TOKEN`

---

## 🚀 Publishing Methods

### **Method 1: CLI (Recommended)**

```bash
# 1. Activate venv
.\venv\Scripts\Activate.ps1

# 2. Install publisher
pip install mcp-publish

# 3. Set GitHub token
$env:GITHUB_TOKEN = "ghp_YOUR_TOKEN"

# 4. Publish
mcp-publish publish \
  --name db-metadata-extractor-mcp \
  --description "Database schema metadata extraction" \
  --python-package db-metadata-extractor-mcp \
  --github-repo yourusername/db-metadata-extractor-mcp
```

### **Method 2: Manual GitHub PR (Backup)**

1. Fork: https://github.com/modelcontextprotocol/servers
2. Clone fork: `git clone https://github.com/YOUR_USERNAME/servers.git`
3. Create branch: `git checkout -b add/db-metadata-extractor-mcp`
4. Create directory: `mkdir src/db_metadata_extractor_mcp`
5. Copy files:
   ```bash
   cp server.json src/db_metadata_extractor_mcp/
   cp SERVER_README.md src/db_metadata_extractor_mcp/README.md
   ```
6. Add index entry:
   - Edit `src/index.json`
   - Add entry for your server
7. Commit: `git add . && git commit -m "Add db-metadata-extractor-mcp server"`
8. Push: `git push origin add/db-metadata-extractor-mcp`
9. Create Pull Request
10. Wait for Anthropic review/merge

---

## 📋 Validation Steps

Before publishing, validate everything:

```bash
# 1. Validate server.json
python -c "import json; json.load(open('server.json')); print('✅ Valid JSON')"

# 2. Check package on PyPI
pip search db-metadata-extractor-mcp

# 3. Test local installation
pip install db-metadata-extractor-mcp --force-reinstall

# 4. Test CLI
db-metadata-extractor-mcp --help

# 5. Test MCP functionality (if client available)
# Should start server in stdio mode ready for connections
db-metadata-extractor-mcp
```

---

## 🔐 Security Notes

1. **Never commit API tokens** to GitHub
2. **Use environment variables** for sensitive credentials
3. **Set GitHub token expiration** to 90 days max
4. **Rotate tokens** regularly
5. **Don't share tokens** in chat/logs

---

## 📱 After Publication

Once published to MCP Registry:

✅ Server listed at: https://registry.modelcontextprotocol.io  
✅ Auto-discoverable in VS Code Agent Mode  
✅ Works in Claude Desktop  
✅ Available in Cline and other MCP clients  
✅ Users can one-click install  

---

## 🔄 Future Updates

To publish a new version:

1. Update `version` in `pyproject.toml`
   ```toml
   version = "0.2.0"
   ```

2. Update `version` in `server.json`
   ```json
   "version": "0.2.0"
   ```

3. Build and publish to PyPI
   ```bash
   python -m build
   python -m twine upload dist/*
   ```

4. Re-publish to MCP Registry
   ```bash
   mcp-publish publish ...  # Same command as before
   ```

---

## 💬 Support

If publication fails:

1. Check `MCP_REGISTRY_GUIDE.md` troubleshooting section
2. Verify `server.json` is valid
3. Ensure PyPI package is published and installable
4. Verify GitHub token has correct scopes
5. Check GitHub repository is public and accessible

---

## Helpful Links

- **MCP Registry**: https://registry.modelcontextprotocol.io
- **MCP Spec**: https://modelcontextprotocol.io/spec
- **Servers Repo**: https://github.com/modelcontextprotocol/servers
- **PyPI Package**: https://pypi.org/project/db-metadata-extractor-mcp/
- **GitHub Tokens**: https://github.com/settings/tokens
- **MCP Publish CLI**: https://github.com/modelcontextprotocol/publish-cli

---

## Next Action

When ready to publish, run:
```bash
cd db-metadata-extractor-mcp
pip install mcp-publish
$env:GITHUB_TOKEN = "ghp_YOUR_TOKEN"
mcp-publish publish --name db-metadata-extractor-mcp --python-package db-metadata-extractor-mcp --github-repo yourusername/db-metadata-extractor-mcp
```

Good luck! 🚀
