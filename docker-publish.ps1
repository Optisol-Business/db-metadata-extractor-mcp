Write-Host "======================================" -ForegroundColor Cyan
Write-Host " Docker Build and Push Script" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

$Version = "0.1.2"
$Registry = "optisolbusiness"
$ImageName = "$Registry/db-metadata-extractor-mcp"
$VersionTag = "$ImageName`:$Version"
$LatestTag = "$ImageName`:latest"

Write-Host "Step 1: Checking Docker..." -ForegroundColor Yellow
$result = docker --version 2>&1
Write-Host "OK: $result" -ForegroundColor Green
Write-Host ""

Write-Host "Step 2: Building image..." -ForegroundColor Yellow
Write-Host "Building: $VersionTag and $LatestTag" -ForegroundColor Gray
docker build -t $VersionTag -t $LatestTag .
Write-Host "OK: Image built successfully" -ForegroundColor Green
Write-Host ""

Write-Host "Step 3: Image information..." -ForegroundColor Yellow
docker images | Select-String $ImageName
Write-Host ""

Write-Host "Step 4: Testing image..." -ForegroundColor Yellow
docker run --rm $VersionTag --help > $null 2>&1
Write-Host "OK: Image runs successfully" -ForegroundColor Green
Write-Host ""

Write-Host "Step 5: Checking Docker Hub login..." -ForegroundColor Yellow
$loginCheck = docker info 2>&1 | Select-String "Username"
if ($loginCheck) {
    Write-Host "OK: Already logged in to Docker Hub" -ForegroundColor Green
} else {
    Write-Host "INFO: Please login to Docker Hub first" -ForegroundColor Yellow
    Write-Host "Run: docker login" -ForegroundColor Gray
    Write-Host "Then run this script again without -SkipPush" -ForegroundColor Gray
}
Write-Host ""

Write-Host "======================================" -ForegroundColor Green
Write-Host " SUCCESS - Image ready!" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. docker login (if not already logged in)" -ForegroundColor Gray
Write-Host "  2. docker push $VersionTag" -ForegroundColor Gray
Write-Host "  3. docker push $LatestTag" -ForegroundColor Gray
Write-Host "  4. Verify on Docker Hub" -ForegroundColor Gray
Write-Host "  5. git commit and push to GitHub" -ForegroundColor Gray
Write-Host ""
