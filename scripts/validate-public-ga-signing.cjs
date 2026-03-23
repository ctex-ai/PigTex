'use strict';

const skipExplicitGuard = process.env.PIGTEX_SKIP_SIGNING_ENV_GUARD === '1';
const certificateLink = (process.env.WIN_CSC_LINK || process.env.CSC_LINK || '').trim();
const certificatePassword = (
    process.env.WIN_CSC_KEY_PASSWORD
    || process.env.CSC_KEY_PASSWORD
    || ''
).trim();

function fail(message) {
    console.error(`[pigtex signing guard] ${message}`);
    process.exit(1);
}

if (skipExplicitGuard) {
    console.warn(
        '[pigtex signing guard] Skipping certificate environment validation because '
        + 'PIGTEX_SKIP_SIGNING_ENV_GUARD=1. electron-builder forceCodeSigning remains enabled.'
    );
    process.exit(0);
}

if (!certificateLink) {
    fail(
        'Public GA packaging requires a Windows code-signing certificate path or URL via '
        + 'WIN_CSC_LINK or CSC_LINK.'
    );
}

if (!certificatePassword) {
    fail(
        'Public GA packaging requires the matching certificate password via '
        + 'WIN_CSC_KEY_PASSWORD or CSC_KEY_PASSWORD.'
    );
}

console.log('[pigtex signing guard] Windows signing inputs detected for public GA packaging.');
