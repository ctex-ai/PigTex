'use strict';

const path = require('path');
const { spawn } = require('child_process');
const { resolveRceditPath } = require('./windows-rcedit-utils.cjs');

async function main() {
    const electronBuilderCliPath = path.join(
        __dirname,
        '..',
        'node_modules',
        'electron-builder',
        'out',
        'cli',
        'cli.js'
    );

    const childEnv = { ...process.env };
    const rceditPath = resolveRceditPath();
    if (rceditPath) {
        childEnv.ELECTRON_BUILDER_RCEDIT_PATH = rceditPath;
        process.stdout.write(`[pigtex build] using rcedit from ${rceditPath}\n`);
    }

    const child = spawn(process.execPath, [electronBuilderCliPath, ...process.argv.slice(2)], {
        stdio: 'inherit',
        cwd: path.join(__dirname, '..'),
        env: childEnv,
        windowsHide: false,
    });

    child.on('exit', (code, signal) => {
        if (signal) {
            process.kill(process.pid, signal);
            return;
        }

        process.exit(code ?? 1);
    });

    child.on('error', (error) => {
        process.stderr.write(`${error.stack || error.message}\n`);
        process.exit(1);
    });
}

main();
