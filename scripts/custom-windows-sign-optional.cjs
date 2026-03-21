'use strict';

const { sign: signWithLocalSigntool } = require('./custom-windows-sign.cjs');

async function sign(configuration) {
    if (!configuration?.cscInfo) {
        return;
    }

    await signWithLocalSigntool(configuration);
}

module.exports = {
    sign,
};
