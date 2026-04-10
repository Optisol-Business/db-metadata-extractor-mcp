# Official mcp-publisher CLI Publishing Guide

## Complete Step-by-Step Instructions

---

## **STEP 1: Prepare Your Environment**

### 1.1 Verify Python & Pip

```powershell
# Check Python version (need 3.8+)
python --version

# Upgrade pip
python -m pip install --upgrade pip
```

### 1.2 Activate Virtual Environment

```powershell
# Navigate to your project
cd db-metadata-extractor-mcp

# Activate venv
.\venv\Scripts\Activate.ps1
```

**Expected output:** Your prompt should show `(venv)` prefix.

---

## **STEP 2: Install mcp-publisher CLI**

### 2.1 Install from NPM (Recommended)

The official mcp-publisher is a Node.js tool:

```powershell
# Install Node.js (if not already installed)
# Download from: https://nodejs.org/
# Or use Winget:
winget install OpenJS.NodeJS

# Verify Node installation
node --version
npm --version
```

### 2.2 Install mcp-publisher Globally

```powershell
# Install globally (so you can use anywhere)
npm install -g @modelcontextprotocol/publisher

# Verify installation
mcp-publisher --version
```

**Expected output:** Version number (e.g., `1.0.0`)

### 2.3 Alternative: Install Locally

```powershell
# Install in project directory
npm install @modelcontextprotocol/publisher

# Use with npx
npx @modelcontextprotocol/publisher --version
```

---

## **STEP 3: Update server.json with Correct Information**

Before publishing, update your server.json:

### 3.1 Update Author Email

```json
"author": {
  "name": "Karpagavalli",
  "email": "your-actual-email@example.com"  // Use your real email
}
```

### 3.2 Update GitHub URLs

Replace `yourusername` with your actual GitHub username:

```json
"homepage": "https://github.com/YOUR_USERNAME/db-metadata-extractor-mcp",
"repository": {
  "type": "git",
  "url": "https://github.com/YOUR_USERNAME/db-metadata-extractor-mcp.git"
}
```

---

## **STEP 4: Validate server.json**

### 4.1 Validate JSON Format

```powershell
# Check JSON is valid
python -c "import json; json.load(open('server.json')); print('✅ Valid JSON')"
```

### 4.2 Validate with mcp-publisher

```powershell
# Validate configuration
mcp-publisher validate

# Or with npx
npx @modelcontextprotocol/publisher validate
```

**Expected output:**
```
✅ server.json is valid
✅ All required fields present
✅ Tool schemas are valid
```

### 4.3 Manual Validation Checklist

Verify your server.json has:

```json
{
  "name": "db-metadata-extractor-mcp",           ✅ Unique identifier
  "version": "0.1.0",                            ✅ Semver format
  "description": "...",                          ✅ < 200 chars
  "author": {                                    ✅ Creator info
    "name": "Karpagavalli",
    "email": "your-email@example.com"           ✅ Valid email
  },
  "license": "MIT",                              ✅ SPDX identifier
  "homepage": "https://github.com/YOUR_USERNAME/...",  ✅ Your repo
  "repository": {                                ✅ Git repo info
    "type": "git",
    "url": "https://github.com/YOUR_USERNAME/..."
  },
  "keywords": [...],                             ✅ 5-10 tags
  "tools": [                                     ✅ At least 1 tool
    {
      "name": "extract_metadata",
      "description": "...",
      "inputSchema": { ... }
    }
  ],
  "requirements": {                              ✅ Dependencies
    "python": ">=3.8",
    "packages": [...]
  }
}
```

---

## **STEP 5: Authentication & Login**

### 5.1 Login to MCP Registry

```powershell
# Authenticate with the registry
mcp-publisher login

# You'll be prompted for:
# - GitHub username or email
# - GitHub personal access token
```

**Or with npx:**

```powershell
npx @modelcontextprotocol/publisher login
```

### 5.2 Create GitHub Personal Access Token

If you don't have a token:

1. Go to: **https://github.com/settings/tokens**
2. Click **"Generate new token (classic)"**
3. Give it a name: `mcp-publisher`
4. Select scopes:
   - ✅ `public_repo` (access public repos)
   - ✅ `user:email` (read email)
5. Click **"Generate token"**
6. **Copy the token:** `ghp_XXXXXXXXXXXXX`

### 5.3 Provide Credentials to CLI

When prompted:

```
GitHub username/email: your-github-username
GitHub token: ghp_YOUR_TOKEN_HERE (paste from Step 5.2)
```

**Expected output:**
```
✅ Authentication successful
✅ Logged in as: your-github-username
```

### 5.4 Verify Login

```powershell
# Check login status
mcp-publisher auth status

# Should show:
# Logged in as: your-github-username
```

---

## **STEP 6: Pre-Publish Validation**

### 6.1 Final Checks

```powershell
# 1. Verify package is on PyPI
pip search db-metadata-extractor-mcp

# 2. Test local installation
pip install db-metadata-extractor-mcp --force-reinstall

# 3. Test server starts
db-metadata-extractor-mcp --help

# 4. Validate server.json once more
mcp-publisher validate
```

### 6.2 Test Run (Optional)

Some versions support dry-run:

```powershell
# Dry run (shows what will be published without publishing)
mcp-publisher publish --dry-run

# Or simulate:
mcp-publisher publish --simulate
```

---

## **STEP 7: Publish to MCP Registry**

### 7.1 Publish Command

```powershell
# Publish your server
mcp-publisher publish

# Or specify server.json location:
mcp-publisher publish ./server.json

# With npx:
npx @modelcontextprotocol/publisher publish
```

### 7.2 What Happens

The CLI will:
1. ✅ Validate server.json
2. ✅ Verify PyPI package exists
3. ✅ Check GitHub repository
4. ✅ Upload to MCP Registry
5. ✅ Return registry URL

### 7.3 Expected Output

```
✅ Validating server.json...
✅ Checking PyPI package: db-metadata-extractor-mcp@0.1.0
✅ Verifying GitHub repository...
✅ Publishing to MCP Registry...
✅ Publication successful!

Registry URL: https://registry.modelcontextprotocol.io/db-metadata-extractor-mcp
```

---

## **STEP 8: Verify Publication**

### 8.1 Check Registry Website

Visit: **https://registry.modelcontextprotocol.io**

Search for: `db-metadata-extractor-mcp`

You should see:
- ✅ Server listed
- ✅ Version: 0.1.0
- ✅ Description visible
- ✅ Installation command shown

### 8.2 Test Installation from Registry

```powershell
# Users should be able to install directly
pip install db-metadata-extractor-mcp

# Test it
db-metadata-extractor-mcp --help
```

### 8.3 Check VS Code Agent Mode

VS Code users should see your server:
1. Open VS Code
2. Agent Mode settings
3. Search for `db-metadata-extractor-mcp`
4. Should appear in list

---

## **STEP 9: Troubleshooting Common Errors**

### **Error 1: "mcp-publisher not found"**

**Cause:** CLI not installed or not in PATH

**Fix:**
```powershell
# Reinstall globally
npm install -g @modelcontextprotocol/publisher

# Or use full path
npx @modelcontextprotocol/publisher publish

# Verify installation
mcp-publisher --version
```

---

### **Error 2: "Invalid server.json"**

**Cause:** JSON format issue or missing required fields

**Fix:**
```powershell
# Validate JSON syntax
python -c "import json; json.load(open('server.json'))"

# Check required fields:
# - name (unique identifier)
# - version (semver)
# - description (< 200 chars)
# - author.email (valid email)
# - license (SPDX)
# - tools (array with at least 1 tool)
```

**Common issues:**
- ❌ Missing comma between fields
- ❌ Unclosed quotes or braces
- ❌ Invalid JSON data types

---

### **Error 3: "Authentication failed"**

**Cause:** Invalid or expired GitHub token

**Fix:**
```powershell
# Logout and login again
mcp-publisher logout
mcp-publisher login

# Or regenerate token:
# 1. Go to https://github.com/settings/tokens
# 2. Delete old token
# 3. Create new token with correct scopes:
#    - public_repo
#    - user:email
# 4. Login again with new token
```

---

### **Error 4: "Package not found on PyPI"**

**Cause:** Package version doesn't exist on PyPI

**Fix:**
```powershell
# Verify package is published
pip index versions db-metadata-extractor-mcp

# Or check online:
# https://pypi.org/project/db-metadata-extractor-mcp/

# If not found, publish to PyPI first:
python -m build
python -m twine upload dist/*
```

---

### **Error 5: "GitHub repository not accessible"**

**Cause:** Repository doesn't exist, is private, or token lacks permission

**Fix:**
```powershell
# 1. Verify repo exists and is PUBLIC:
#    https://github.com/YOUR_USERNAME/db-metadata-extractor-mcp

# 2. Check token has public_repo scope:
#    Go to https://github.com/settings/tokens
#    Edit token → Check ✅ public_repo

# 3. Regenerate token if needed:
#    - Delete old token
#    - Create new one with correct scopes
#    - Login again: mcp-publisher login
```

---

### **Error 6: "Server name already exists in registry"**

**Cause:** Another server with same name exists

**Fix:**
```json
// Option 1: Use unique name in server.json
"name": "db-metadata-extractor-mcp-yourname"

// Option 2: Contact registry maintainers
// Email: mcp@anthropic.com

// Option 3: Wait for approval if it's your updated version
// (Registry might prevent duplicates until next release cycle)
```

---

## **Best Practices for Successful Publishing**

### **✅ DO:**

1. **Test locally first**
   ```powershell
   mcp-publisher validate
   ```

2. **Use semver versioning**
   ```
   0.1.0  ✅ Correct
   1.2.3  ✅ Correct
   2.0.0-beta ✅ Correct with pre-release
   2.0    ❌ Incorrect (missing patch)
   ```

3. **Keep description concise**
   - Max 200 characters
   - No special formatting
   - Clear and descriptive

4. **Use proper email**
   - Real, accessible email
   - Users will contact you
   - Use company/github email

5. **Maintain GitHub repo**
   - Keep public
   - Include README
   - Have working code

6. **Test all tools**
   ```powershell
   # Ensure tools work correctly
   db-metadata-extractor-mcp
   # Test each tool before publishing
   ```

7. **Document dependencies**
   - List all requirements
   - Specify version constraints
   - Include optional dependencies

### **❌ DON'T:**

1. ❌ Include sensitive data in server.json
2. ❌ Use private GitHub repository
3. ❌ Publish to registry before PyPI
4. ❌ Use invalid semver versions
5. ❌ Make repository private after publishing
6. ❌ Change package name without updating registry
7. ❌ Include absolute file paths in schema

---

## **Quick Start Checklist**

```bash
# Copy and paste commands in order:

# 1. Install Node.js + npm
# (Download from https://nodejs.org/)

# 2. Install mcp-publisher
npm install -g @modelcontextprotocol/publisher

# 3. Activate venv
cd db-metadata-extractor-mcp
.\venv\Scripts\Activate.ps1

# 4. Update server.json
# (Edit author email and GitHub URLs)

# 5. Validate
mcp-publisher validate

# 6. Login
mcp-publisher login
# Enter GitHub username/email
# Enter GitHub token (ghp_...)

# 7. Verify PyPI package
pip index versions db-metadata-extractor-mcp

# 8. Publish
mcp-publisher publish

# 9. Verify
# Visit: https://registry.modelcontextprotocol.io/db-metadata-extractor-mcp
```

---

## **After Successful Publishing**

🎉 **Your server is now live!**

Users can:
- Install: `pip install db-metadata-extractor-mcp`
- Discover in VS Code Agent Mode
- Use in Claude Desktop
- Access via MCP-compatible clients

---

## **Updating Your Server**

When you release version 0.2.0:

```powershell
# 1. Update version in server.json
"version": "0.2.0"

# 2. Update version in pyproject.toml
version = "0.2.0"

# 3. Build and publish to PyPI
python -m build
python -m twine upload dist/*

# 4. Publish to MCP Registry
mcp-publisher publish

# Done! Users get auto-discovery of new version
```

---

## **Support & Resources**

- **MCP Registry**: https://registry.modelcontextprotocol.io
- **MCP Spec**: https://modelcontextprotocol.io/
- **Publisher Repo**: https://github.com/modelcontextprotocol/python-sdk
- **Issues**: https://github.com/modelcontextprotocol/servers/issues

---

**Ready? Start with Step 1! 🚀**
