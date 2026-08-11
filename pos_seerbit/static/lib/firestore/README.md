# Firestore SDK Files

This directory contains the Firestore SDK JavaScript files for local use in the Seerbit Odoo module.

## Files

- **firebase-app-compat.js** - Firebase App SDK (Core functionality)
- **firebase-firestore-compat.js** - Firestore Database SDK

## Version

Firebase SDK Version: 9.22.0 (Compatibility Mode)

## Source

These files were downloaded from the official Firebase CDN:
- https://www.gstatic.com/firebasejs/9.22.0/firebase-app-compat.js
- https://www.gstatic.com/firebasejs/9.22.0/firebase-firestore-compat.js

## Benefits of Local Files

1. **Reliability**: No dependency on external CDN availability
2. **Performance**: Faster loading as files are served from Odoo server
3. **Offline Support**: Works even without internet connection
4. **Version Control**: Specific version is always available
5. **Security**: No external dependencies

## Firestore vs Firebase Realtime Database

This module now uses **Firestore** instead of Firebase Realtime Database:

### Firestore Advantages:
- **Better Querying**: More powerful query capabilities
- **Scalability**: Better for large datasets
- **Offline Support**: Better offline data synchronization
- **Security**: More granular security rules
- **Future-Proof**: Google's recommended database solution

### Configuration Changes:
- Uses `projectId` instead of `databaseURL`
- Uses `collection()` and `document()` instead of `ref()`
- Uses `onSnapshot()` for real-time listeners
- Uses `set()` for document creation

## Updating

To update to a newer version:

1. Download new files from Firebase CDN
2. Replace the existing files in this directory
3. Update the version number in the manifest if needed

## Usage

These files are automatically loaded by Odoo when the Seerbit module is installed.
The files are referenced in `pos_seerbit/__manifest__.py` under the `assets` section. 