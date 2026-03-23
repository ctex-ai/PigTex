'use strict';

const fs = require('fs');
const path = require('path');

function fail(message) {
    console.error(`[pigtex release stage] ${message}`);
    process.exit(1);
}

const repoRoot = path.resolve(__dirname, '..', '..');
const desktopRoot = path.resolve(__dirname, '..');
const desktopPackage = JSON.parse(fs.readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'));
const version = typeof desktopPackage.version === 'string' ? desktopPackage.version.trim() : '';

if (!version) {
    fail('Desktop package.json is missing a valid version.');
}

const releaseDir = path.join(desktopRoot, 'release');
const deployDownloadsDir = path.join(repoRoot, 'deploy', 'pigtex', 'downloads');
const stableExeName = `PigTex-${version}.exe`;
const stableBlockmapName = `PigTex-${version}.exe.blockmap`;
const stableExeSourcePath = path.join(releaseDir, stableExeName);
const stableBlockmapSourcePath = path.join(releaseDir, stableBlockmapName);

if (!fs.existsSync(stableExeSourcePath)) {
    fail(
        `Stable installer not found at ${stableExeSourcePath}. `
        + 'Run npm run build:win:release first. Preview artifacts are not accepted for release staging.'
    );
}

if (!fs.existsSync(stableBlockmapSourcePath)) {
    fail(
        `Stable blockmap not found at ${stableBlockmapSourcePath}. `
        + 'Run npm run build:win:release first so staged auto-update metadata matches the installer.'
    );
}

fs.mkdirSync(deployDownloadsDir, { recursive: true });

const stagedExePath = path.join(deployDownloadsDir, stableExeName);
const stagedBlockmapPath = path.join(deployDownloadsDir, stableBlockmapName);

fs.copyFileSync(stableExeSourcePath, stagedExePath);
fs.copyFileSync(stableBlockmapSourcePath, stagedBlockmapPath);

const exeStats = fs.statSync(stagedExePath);
const blockmapStats = fs.statSync(stagedBlockmapPath);

console.log(`[pigtex release stage] Staged ${stableExeName} (${exeStats.size} bytes)`);
console.log(`[pigtex release stage] Staged ${stableBlockmapName} (${blockmapStats.size} bytes)`);
