'use strict';

const fs = require('fs');
const path = require('path');
const { runRcedit } = require('./windows-rcedit-utils.cjs');

async function editWindowsResources(context) {
    if (process.platform !== 'win32' || context.electronPlatformName !== 'win32') {
        return;
    }

    const appInfo = context.packager.appInfo;
    const executableName = `${appInfo.productFilename || appInfo.productName}.exe`;
    const executablePath = path.join(context.appOutDir, executableName);
    if (!fs.existsSync(executablePath)) {
        throw new Error(`Packaged Windows executable was not found at ${executablePath}`);
    }

    const iconPath = path.join(context.packager.projectDir, 'assets', 'pigtex_logo.ico');
    if (!fs.existsSync(iconPath)) {
        throw new Error(`Windows icon asset was not found at ${iconPath}`);
    }

    const packageJson = context.packager.metadata || {};
    const companyName = typeof packageJson.author === 'string' ? packageJson.author : appInfo.productName;
    const version = appInfo.version;

    await runRcedit([
        executablePath,
        '--set-icon', iconPath,
        '--set-version-string', 'FileDescription', appInfo.productName,
        '--set-version-string', 'ProductName', appInfo.productName,
        '--set-version-string', 'CompanyName', companyName,
        '--set-file-version', version,
        '--set-product-version', version,
    ]);
}

async function afterPack(context) {
    await editWindowsResources(context);
}

module.exports = afterPack;
module.exports.editWindowsResources = editWindowsResources;
