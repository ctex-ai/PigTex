'use strict';

const fs = require('fs');
const path = require('path');

function fail(message) {
    console.error(`[pigtex release stage] ${message}`);
    process.exit(1);
}

const desktopRoot = path.resolve(__dirname, '..', '..');
const desktopPackage = JSON.parse(fs.readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'));
const version = typeof desktopPackage.version === 'string' ? desktopPackage.version.trim() : '';

if (!version) {
    fail('Desktop package.json is missing a valid version.');
}

const releaseDir = path.join(desktopRoot, 'release');
const stagedOutputDir = path.join(desktopRoot, 'release-staged');
const stableExeName = `PigTex-${version}.exe`;
const stableBlockmapName = `PigTex-${version}.exe.blockmap`;
const latestManifestName = 'latest.yml';
const stableExeSourcePath = path.join(releaseDir, stableExeName);
const stableBlockmapSourcePath = path.join(releaseDir, stableBlockmapName);
const latestManifestSourcePath = path.join(releaseDir, latestManifestName);

if (!fs.existsSync(stableExeSourcePath)) {
    fail(
        `Stable installer not found at ${stableExeSourcePath}. `
        + 'Run npm run build:win:release first. Preview artifacts are not accepted for stable staging.'
    );
}

if (!fs.existsSync(stableBlockmapSourcePath)) {
    fail(
        `Stable blockmap not found at ${stableBlockmapSourcePath}. `
        + 'Run npm run build:win:release first so the staged blockmap matches the installer.'
    );
}

if (!fs.existsSync(latestManifestSourcePath)) {
    fail(
        `Update manifest not found at ${latestManifestSourcePath}. `
        + 'Run npm run build:win:release first so auto-update metadata is generated for GitHub Releases.'
    );
}

fs.mkdirSync(stagedOutputDir, { recursive: true });

const stagedExePath = path.join(stagedOutputDir, stableExeName);
const stagedBlockmapPath = path.join(stagedOutputDir, stableBlockmapName);
const stagedLatestManifestPath = path.join(stagedOutputDir, latestManifestName);

fs.copyFileSync(stableExeSourcePath, stagedExePath);
fs.copyFileSync(stableBlockmapSourcePath, stagedBlockmapPath);
fs.copyFileSync(latestManifestSourcePath, stagedLatestManifestPath);

const exeStats = fs.statSync(stagedExePath);
const blockmapStats = fs.statSync(stagedBlockmapPath);
const latestManifestStats = fs.statSync(stagedLatestManifestPath);

console.log(`[pigtex release stage] Staged ${stableExeName} (${exeStats.size} bytes)`);
console.log(`[pigtex release stage] Staged ${stableBlockmapName} (${blockmapStats.size} bytes)`);
console.log(`[pigtex release stage] Staged ${latestManifestName} (${latestManifestStats.size} bytes)`);
console.log(`[pigtex release stage] Output directory: ${stagedOutputDir}`);
