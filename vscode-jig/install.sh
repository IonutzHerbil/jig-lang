#!/bin/bash

# Install Jig Language Extension for VS Code

EXTENSION_PATH="$HOME/.vscode/extensions/jig-lang"

echo "Installing Jig Language Extension for VS Code..."

# Create extensions directory if it doesn't exist
mkdir -p "$HOME/.vscode/extensions"

# Remove old installation if exists
if [ -d "$EXTENSION_PATH" ]; then
    echo "Removing previous installation..."
    rm -rf "$EXTENSION_PATH"
fi

# Copy extension files
echo "Copying extension files..."
cp -r "$(dirname "$0")" "$EXTENSION_PATH"

echo ""
echo "✓ Jig Language Extension installed successfully!"
echo ""
echo "Next steps:"
echo "1. Reload VS Code (Ctrl+Shift+P → 'Developer: Reload Window')"
echo "2. Open a .jig file to see syntax highlighting"
echo ""
