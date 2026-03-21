'use strict';

// ── Load .env file if present (no external dependency needed) ──
const fsNode = require('fs');
const pathNode = require('path');

const envFilePath = pathNode.resolve(__dirname, '..', '.env');
if (fsNode.existsSync(envFilePath)) {
    const envContent = fsNode.readFileSync(envFilePath, 'utf8');
    for (const line of envContent.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const eqIdx = trimmed.indexOf('=');
        if (eqIdx === -1) continue;
        const key = trimmed.slice(0, eqIdx).trim();
        let value = trimmed.slice(eqIdx + 1).trim();
        // Strip surrounding quotes if present
        if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
            value = value.slice(1, -1);
        }
        // Only set if not already defined in the environment
        if (process.env[key] === undefined) {
            process.env[key] = value;
        }
    }
}

const rawApiBase = (process.env.VITE_PIGTEX_API_BASE || '').trim();
const rawAllowLocalhostPackaging =
    process.env.VITE_PIGTEX_ALLOW_LOCALHOST_API_BASE
    || process.env.PIGTEX_ALLOW_LOCALHOST_API_BASE
    || '';
const allowLocalhostPackaging =
    rawAllowLocalhostPackaging === '1'
    || rawAllowLocalhostPackaging.toLowerCase() === 'true';

function fail(message) {
    console.error(`[pigtex build guard] ${message}`);
    process.exit(1);
}

if (!rawApiBase) {
    if (allowLocalhostPackaging) {
        console.warn('[pigtex build guard] VITE_PIGTEX_API_BASE is empty, but localhost packaging override is enabled.');
        process.exit(0);
    }
    fail(
        'VITE_PIGTEX_API_BASE is required for packaged desktop builds. '
        + 'Set it to the hosted backend root URL, or explicitly set PIGTEX_ALLOW_LOCALHOST_API_BASE=1 for local QA packaging.'
    );
}

let parsed;
try {
    parsed = new URL(rawApiBase);
} catch {
    fail('VITE_PIGTEX_API_BASE must be an absolute http(s) URL.');
}

if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    fail('VITE_PIGTEX_API_BASE must use http:// or https://.');
}

const normalizedHostname = (parsed.hostname || '').trim().toLowerCase();
const isLoopbackTarget =
    normalizedHostname === 'localhost'
    || normalizedHostname === '127.0.0.1'
    || normalizedHostname === '::1'
    || normalizedHostname === '[::1]';

if (isLoopbackTarget && !allowLocalhostPackaging) {
    fail(
        'VITE_PIGTEX_API_BASE cannot point to localhost or loopback for packaged builds. '
        + 'If this is an intentional local QA package, set PIGTEX_ALLOW_LOCALHOST_API_BASE=1.'
    );
}

console.log(
    `[pigtex build guard] Packaging against ${rawApiBase}${isLoopbackTarget ? ' (localhost override enabled)' : ''}`
);
