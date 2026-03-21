'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

function fail(message) {
    console.error(`[pigtex release publish] ${message}`);
    process.exit(1);
}

function parseOptionalArg(flagName) {
    const index = process.argv.indexOf(flagName);
    if (index === -1) {
        return null;
    }

    return process.argv[index + 1] || null;
}

function resolveGhExecutable() {
    const candidates = [
        process.env.GH_PATH,
        'gh',
        path.join(process.env.ProgramFiles || 'C:\\Program Files', 'GitHub CLI', 'gh.exe'),
    ].filter(Boolean);

    for (const candidate of candidates) {
        const result = spawnSync(candidate, ['--version'], {
            stdio: 'ignore',
            shell: false,
        });
        if (result.status === 0) {
            return candidate;
        }
    }

    fail('GitHub CLI was not found. Install gh or set GH_PATH.');
}

function getGitHubToken() {
    if (process.env.GH_TOKEN) {
        return process.env.GH_TOKEN;
    }

    const credentialResult = spawnSync('git', ['credential', 'fill'], {
        input: 'protocol=https\nhost=github.com\n\n',
        encoding: 'utf8',
        shell: false,
    });

    if (credentialResult.status !== 0 || !credentialResult.stdout) {
        return null;
    }

    const passwordLine = credentialResult.stdout
        .split(/\r?\n/)
        .find((line) => line.startsWith('password='));

    if (!passwordLine) {
        return null;
    }

    return passwordLine.slice('password='.length).trim() || null;
}

function runGh(ghPath, args, env) {
    const result = spawnSync(ghPath, args, {
        stdio: 'inherit',
        env,
        shell: false,
    });

    if (result.error) {
        fail(result.error.message);
    }

    return result.status ?? 1;
}

function runGhStatus(ghPath, args, env) {
    const result = spawnSync(ghPath, args, {
        stdio: 'pipe',
        encoding: 'utf8',
        env,
        shell: false,
    });

    if (result.error) {
        fail(result.error.message);
    }

    return result.status ?? 1;
}

const desktopRoot = path.resolve(__dirname, '..', '..');
const desktopPackage = JSON.parse(fs.readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'));
const version = typeof desktopPackage.version === 'string' ? desktopPackage.version.trim() : '';

if (!version) {
    fail('Desktop package.json is missing a valid version.');
}

const repo = parseOptionalArg('--repo') || 'ctex-ai/PigTex';
const tag = parseOptionalArg('--tag') || `v${version}`;
const releaseTitle = parseOptionalArg('--title') || `PigTex ${version}`;
const stagedOutputDir = path.join(desktopRoot, 'release-staged');
const assets = [
    path.join(stagedOutputDir, `PigTex-${version}.exe`),
    path.join(stagedOutputDir, `PigTex-${version}.exe.blockmap`),
    path.join(stagedOutputDir, 'latest.yml'),
];

for (const assetPath of assets) {
    if (!fs.existsSync(assetPath)) {
        fail(`Missing staged release asset: ${assetPath}. Run npm run release:stage first.`);
    }
}

const ghPath = resolveGhExecutable();
const ghToken = getGitHubToken();
if (!ghToken) {
    fail('No GitHub token was available through GH_TOKEN or the git credential helper.');
}

const ghEnv = {
    ...process.env,
    GH_TOKEN: ghToken,
};

const prerelease = version.includes('-');
const viewStatus = runGhStatus(ghPath, ['release', 'view', tag, '--repo', repo], ghEnv);

if (viewStatus === 0) {
    const uploadStatus = runGh(
        ghPath,
        ['release', 'upload', tag, ...assets, '--repo', repo, '--clobber'],
        ghEnv
    );
    if (uploadStatus !== 0) {
        process.exit(uploadStatus);
    }

    console.log(`[pigtex release publish] Updated existing GitHub release ${tag} on ${repo}`);
    process.exit(0);
}

const createArgs = [
    'release',
    'create',
    tag,
    ...assets,
    '--repo',
    repo,
    '--target',
    'main',
    '--title',
    releaseTitle,
    '--generate-notes',
];

if (prerelease) {
    createArgs.push('--prerelease');
} else {
    createArgs.push('--latest');
}

const createStatus = runGh(ghPath, createArgs, ghEnv);
if (createStatus !== 0) {
    process.exit(createStatus);
}

console.log(`[pigtex release publish] Published GitHub release ${tag} to ${repo}`);
