'use strict';

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

function hasRceditBinaries(candidatePath) {
    if (!candidatePath) {
        return false;
    }

    return ['rcedit-ia32.exe', 'rcedit-x64.exe']
        .every((fileName) => fs.existsSync(path.join(candidatePath, fileName)));
}

function findCachedRceditPath() {
    const cacheRoot = path.join(
        process.env.LOCALAPPDATA || '',
        'electron-builder',
        'Cache',
        'winCodeSign'
    );
    if (!cacheRoot || !fs.existsSync(cacheRoot)) {
        return null;
    }

    const candidateDirs = fs.readdirSync(cacheRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => path.join(cacheRoot, entry.name))
        .filter(hasRceditBinaries)
        .sort((left, right) => fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs);

    return candidateDirs[0] || null;
}

function resolveRceditPath() {
    const explicitPath = (process.env.ELECTRON_BUILDER_RCEDIT_PATH || '').trim();
    if (hasRceditBinaries(explicitPath)) {
        return explicitPath;
    }

    const repoVendorPath = path.join(__dirname, '..', '..', 'vendor', 'rcedit');
    if (hasRceditBinaries(repoVendorPath)) {
        return repoVendorPath;
    }

    const cachedPath = findCachedRceditPath();
    if (cachedPath) {
        return cachedPath;
    }

    throw new Error(
        'RCEdit binaries were not found. Set ELECTRON_BUILDER_RCEDIT_PATH or provide a cached rcedit bundle.'
    );
}

async function runRcedit(args) {
    const rceditDir = resolveRceditPath();
    const binaryPath = path.join(rceditDir, process.arch === 'ia32' ? 'rcedit-ia32.exe' : 'rcedit-x64.exe');

    try {
        await execFileAsync(binaryPath, args, {
            windowsHide: true,
            maxBuffer: 10 * 1024 * 1024,
        });
    } catch (error) {
        const stdout = typeof error.stdout === 'string' ? error.stdout.trim() : '';
        const stderr = typeof error.stderr === 'string' ? error.stderr.trim() : '';
        const details = [stdout, stderr].filter(Boolean).join('\n');
        throw new Error(details ? `rcedit failed:\n${details}` : 'rcedit failed without output');
    }
}

module.exports = {
    hasRceditBinaries,
    findCachedRceditPath,
    resolveRceditPath,
    runRcedit,
};
