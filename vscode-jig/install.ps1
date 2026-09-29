# Install Jig Language Extension for VS Code

$extensionPath = "$env:USERPROFILE\.vscode\extensions\jig-lang"

Write-Host "Installing Jig Language Extension for VS Code..." -ForegroundColor Cyan

# Create extensions directory if it doesn't exist
if (-not (Test-Path "$env:USERPROFILE\.vscode\extensions")) {
    New-Item -ItemType Directory -Path "$env:USERPROFILE\.vscode\extensions" -Force | Out-Null
}

# Remove old installation if exists
if (Test-Path $extensionPath) {
    Write-Host "Removing previous installation..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $extensionPath
}

# Copy extension files
Write-Host "Copying extension files..." -ForegroundColor Green
Copy-Item -Recurse -Force $PSScriptRoot $extensionPath

Write-Host ""
Write-Host "Success! Jig Language Extension installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Reload VS Code (Ctrl+Shift+P -> 'Developer: Reload Window')" -ForegroundColor White
Write-Host "2. Open a .jig file to see syntax highlighting" -ForegroundColor White
Write-Host ""
