'use strict';

const fs = require('fs/promises');
const path = require('path');
const { editWindowsResources } = require('./after-pack-edit-windows-resources.cjs');

const SIGNABLE_EXTENSIONS = new Set(['.exe', '.dll', '.node']);

async function collectSignableFiles(rootDir) {
    const results = [];
    const queue = [rootDir];

    while (queue.length > 0) {
        const currentDir = queue.pop();
        const entries = await fs.readdir(currentDir, { withFileTypes: true });
        for (const entry of entries) {
            const fullPath = path.join(currentDir, entry.name);
            if (entry.isDirectory()) {
                queue.push(fullPath);
                continue;
            }

            if (SIGNABLE_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
                results.push(fullPath);
            }
        }
    }

    results.sort((left, right) => left.localeCompare(right));
    return results;
}

async function afterPack(context) {
    if (process.platform !== 'win32' || context.electronPlatformName !== 'win32') {
        return;
    }

    await editWindowsResources(context);

    if (typeof context.packager?.signIf !== 'function') {
        throw new Error('Windows afterPack signing requires a packager with signIf(file).');
    }

    const signableFiles = await collectSignableFiles(context.appOutDir);
    for (const filePath of signableFiles) {
        await context.packager.signIf(filePath);
    }
}

module.exports = afterPack;
