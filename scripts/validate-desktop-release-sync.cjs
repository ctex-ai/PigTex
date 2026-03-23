'use strict';

const fs = require('fs');
const path = require('path');

function fail(message) {
    console.error(`[pigtex release guard] ${message}`);
    process.exit(1);
}

const desktopPackagePath = path.resolve(__dirname, '..', 'package.json');
const websiteDownloadPath = path.resolve(__dirname, '..', '..', 'Website', 'src', 'lib', 'download.ts');

if (!fs.existsSync(desktopPackagePath)) {
    fail(`Missing desktop package manifest at ${desktopPackagePath}`);
}

if (!fs.existsSync(websiteDownloadPath)) {
    fail(`Missing website download metadata file at ${websiteDownloadPath}`);
}

const desktopPackage = JSON.parse(fs.readFileSync(desktopPackagePath, 'utf8'));
const desktopVersion = typeof desktopPackage.version === 'string' ? desktopPackage.version.trim() : '';
if (!desktopVersion) {
    fail('Desktop package.json is missing a valid version.');
}

const websiteDownloadSource = fs.readFileSync(websiteDownloadPath, 'utf8');
const websiteVersionMatch = websiteDownloadSource.match(/PIGTEX_WINDOWS_VERSION\s*=\s*"([^"]+)"/);
const websiteVersion = websiteVersionMatch?.[1]?.trim() || '';

if (!websiteVersion) {
    fail('Website download metadata is missing PIGTEX_WINDOWS_VERSION.');
}

if (desktopVersion !== websiteVersion) {
    fail(
        `Desktop version (${desktopVersion}) does not match website download version (${websiteVersion}). `
        + 'Update both before building a release.'
    );
}

console.log(`[pigtex release guard] Desktop and website release versions are synchronized at ${desktopVersion}.`);