# Firebase SDK Files

This directory contains the Firebase SDK JavaScript files for local use in the Seerbit Odoo module.

## Files

- **firebase-app-compat.js** - Firebase App SDK (Core functionality)
- **firebase-database-compat.js** - Firebase Realtime Database SDK

## Version

Firebase SDK Version: 9.22.0 (Compatibility Mode)

## Source

These files were downloaded from the official Firebase CDN:
- https://www.gstatic.com/firebasejs/9.22.0/firebase-app-compat.js
- https://www.gstatic.com/firebasejs/9.22.0/firebase-database-compat.js

## Benefits of Local Files

1. **Reliability**: No dependency on external CDN availability
2. **Performance**: Faster loading as files are served from Odoo server
3. **Offline Support**: Works even without internet connection
4. **Version Control**: Specific version is always available
5. **Security**: No external dependencies

## Updating

To update to a newer version:

1. Download new files from Firebase CDN
2. Replace the existing files in this directory
3. Update the version number in the manifest if needed

## Usage

These files are automatically loaded by Odoo when the Seerbit module is installed.
The files are referenced in `pos_seerbit/__manifest__.py` under the `assets` section. 