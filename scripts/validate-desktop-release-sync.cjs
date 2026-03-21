'use strict';

const fs = require('fs');
const path = require('path');

function fail(message) {
    console.error(`[pigtex release guard] ${message}`);
    process.exit(1);
}

const desktopPackagePath = path.resolve(__dirname, '..', 'package.json');
const releaseConfigPath = path.resolve(__dirname, '..', 'electron-builder.release.json');

if (!fs.existsSync(desktopPackagePath)) {
    fail(`Missing desktop package manifest at ${desktopPackagePath}`);
}

if (!fs.existsSync(releaseConfigPath)) {
    fail(`Missing desktop release config at ${releaseConfigPath}`);
}

const desktopPackage = JSON.parse(fs.readFileSync(desktopPackagePath, 'utf8'));
const desktopVersion = typeof desktopPackage.version === 'string' ? desktopPackage.version.trim() : '';
if (!desktopVersion) {
    fail('Desktop package.json is missing a valid version.');
}

const releaseConfig = JSON.parse(fs.readFileSync(releaseConfigPath, 'utf8'));
const artifactName = releaseConfig?.nsis?.artifactName || '';

if (!artifactName) {
    fail('Release config is missing nsis.artifactName.');
}

if (!artifactName.includes('${version}')) {
    fail('Release artifactName must include ${version}.');
}

if (artifactName.includes('preview')) {
    fail('Release artifactName must not contain preview markers.');
}

console.log(`[pigtex release guard] Desktop release config looks valid for version ${desktopVersion}.`);
