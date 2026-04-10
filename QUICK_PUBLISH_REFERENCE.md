# mcp-publisher Quick Command Reference

## One-Liner Installation & Publishing

```powershell
# Run these in order:

# 1. Install Node.js (if needed)
winget install OpenJS.NodeJS

# 2. Install mcp-publisher
npm install -g @modelcontextprotocol/publisher

# 3. Navigate to project
cd db-metadata-extractor-mcp

# 4. Activate venv
.\venv\Scripts\Activate.ps1

# 5. Login (enters interactive mode)
mcp-publisher login
# Paste your GitHub username
# Paste your GitHub token (ghp_...)

# 6. Validate before publishing
mcp-publisher validate

# 7. PUBLISH to registry
mcp-publisher publish

# 8. Check status
mcp-publisher auth status
```

---

## Verification Commands

```powershell
# Check everything is ready:

# Is Node.js installed?
node --version

# Is mcp-publisher installed?
mcp-publisher --version

# Is server.json valid?
python -c "import json; json.load(open('server.json')); print('✅ Valid')"

# Is PyPI package available?
pip index versions db-metadata-extractor-mcp

# Are you authenticated?
mcp-publisher auth status
```

---

## Common Issues & Fixes

| Issue | Quick Fix |
|-------|-----------|
| `mcp-publisher: command not found` | `npm install -g @modelcontextprotocol/publisher` |
| `Authentication failed` | `mcp-publisher login` (regenerate GitHub token if needed) |
| `Invalid server.json` | Run `mcp-publisher validate` and fix errors |
| `Package not on PyPI` | Run `python -m build && python -m twine upload dist/*` |
| `Token expired` | Go to https://github.com/settings/tokens, delete old, create new |

---

## Before You Start

**Update server.json with YOUR information:**

```json
{
  "author": {
    "name": "Karpagavalli",
    "email": "YOUR_ACTUAL_EMAIL@example.com"  // ⚠️ CHANGE THIS
  },
  "homepage": "https://github.com/YOUR_USERNAME/db-metadata-extractor-mcp",  // ⚠️ CHANGE THIS
  "repository": {
    "type": "git",
    "url": "https://github.com/YOUR_USERNAME/db-metadata-extractor-mcp.git"  // ⚠️ CHANGE THIS
  }
}
```

---

## GitHub Token Setup (if needed)

1. Go to: https://github.com/settings/tokens
2. Click **"Generate new token (classic)"**
3. Name: `mcp-publisher`
4. Scopes needed:
   - ☑ `public_repo`
   - ☑ `user:email`
5. Click **"Generate token"**
6. **Copy token** (it looks like: `ghp_XXXXXXXXXXXXX`)
7. Use in `mcp-publisher login` when prompted

---

## Success Indicators

After running `mcp-publisher publish`, you should see:

```
✅ Validating server.json...
✅ Checking PyPI package...
✅ Verifying GitHub repository...
✅ Publishing to MCP Registry...
✅ Publication successful!

Registry URL: https://registry.modelcontextprotocol.io/db-metadata-extractor-mcp

Your server is now live! Users can discover it in:
- VS Code Agent Mode
- Claude Desktop  
- MCP Registry website
- All MCP-compatible clients
```

---

## What Happens Next

1. **Immediate**: Server listed in registry ✅
2. **Within minutes**: Shows in VS Code Agent Mode
3. **Within hours**: Available in Claude Desktop
4. **Users can install**: `pip install db-metadata-extractor-mcp`

---

## Troubleshooting Flow

```
Is mcp-publisher installed?
├─ NO → Run: npm install -g @modelcontextprotocol/publisher
└─ YES → Continue

Is server.json valid?
├─ NO → Run: mcp-publisher validate (shows errors)
└─ YES → Continue

Are you logged in?
├─ NO → Run: mcp-publisher login
└─ YES → Continue

Is PyPI package published?
├─ NO → Run: python -m build && python -m twine upload dist/*
└─ YES → Ready to publish!

Ready to publish?
└─ RUN: mcp-publisher publish
```

---

## For Full Details

See: **MCP_PUBLISHER_GUIDE.md** (comprehensive guide with all steps)

---

**You're ready! Navigate to your project and start with:**

```powershell
npm install -g @modelcontextprotocol/publisher
mcp-publisher login
mcp-publisher publish
```

🚀 **Good luck publishing!**
