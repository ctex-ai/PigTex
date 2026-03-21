'use strict';

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

function getSignToolCandidates() {
    const candidates = [];
    const explicitPath = (process.env.PIGTEX_SIGNTOOL_PATH || '').trim();
    if (explicitPath) {
        candidates.push(explicitPath);
    }

    const kitsRoot = 'C:\\Program Files (x86)\\Windows Kits\\10\\bin';
    if (!fs.existsSync(kitsRoot)) {
        return candidates;
    }

    const versionDirs = fs.readdirSync(kitsRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name)
        .sort((left, right) => right.localeCompare(left, undefined, { numeric: true }));

    for (const versionDir of versionDirs) {
        candidates.push(path.join(kitsRoot, versionDir, 'x64', 'signtool.exe'));
    }

    return candidates;
}

function resolveSignToolPath() {
    const candidate = getSignToolCandidates().find((item) => fs.existsSync(item));
    if (candidate) {
        return candidate;
    }

    throw new Error(
        'Windows signtool.exe was not found. Set PIGTEX_SIGNTOOL_PATH or install the Windows SDK signing tools.'
    );
}

async function runSignTool(args) {
    const signToolPath = resolveSignToolPath();
    try {
        await execFileAsync(signToolPath, args, {
            windowsHide: true,
            maxBuffer: 10 * 1024 * 1024,
        });
    } catch (error) {
        const stdout = typeof error.stdout === 'string' ? error.stdout.trim() : '';
        const stderr = typeof error.stderr === 'string' ? error.stderr.trim() : '';
        const details = [stdout, stderr].filter(Boolean).join('\n');
        throw new Error(details ? `signtool failed:\n${details}` : 'signtool failed without output');
    }
}

function withRequiredDigestFlags(args, hash) {
    const normalizedArgs = [...args];
    const inputPath = normalizedArgs.pop();
    const hasFileDigestFlag = normalizedArgs.some((value, index) =>
        value.toLowerCase() === '/fd' && typeof normalizedArgs[index + 1] === 'string'
    );
    if (!hasFileDigestFlag) {
        normalizedArgs.push('/fd', (hash || 'sha256').toLowerCase());
    }

    const usesRfc3161Timestamp = normalizedArgs.some((value) => value.toLowerCase() === '/tr');
    const hasTimestampDigestFlag = normalizedArgs.some((value, index) =>
        value.toLowerCase() === '/td' && typeof normalizedArgs[index + 1] === 'string'
    );
    if (usesRfc3161Timestamp && !hasTimestampDigestFlag) {
        normalizedArgs.push('/td', 'sha256');
    }

    normalizedArgs.push(inputPath);
    return normalizedArgs;
}

async function sign(configuration) {
    if (!configuration?.cscInfo) {
        throw new Error(
            'No Windows code-signing certificate is configured. Provide WIN_CSC_LINK/CSC_LINK and the matching password.'
        );
    }

    const args = withRequiredDigestFlags(configuration.computeSignToolArgs(true), configuration.hash);
    await runSignTool(args);
}

module.exports = {
    sign,
    runSignTool,
    resolveSignToolPath,
};
